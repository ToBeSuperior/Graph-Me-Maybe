// A conversation in three panes: the conversations kept, its turns over the box it is asked in,
// and the document picked from a turn on the right. On a narrow screen the conversations are a
// menu and the document a sheet. A conversation asks about its scope -- every document, or those
// chosen in it or dropped on its box -- and keeps it from turn to turn.
import { FolderSearch, MessagesSquare, Plus } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { api, call, errorText } from '../../api/client';
import { Empty, Sheet, Skeleton } from '../../components/ui';
import { shownName } from '../../lib/format';
import { citedFiles, hopTrail } from '../../lib/hops';
import { useT } from '../../lib/i18n';
import { useMediaQuery } from '../../lib/useMediaQuery';
import { Preview } from '../search/Preview';
import { firstRow, type Row } from '../search/ResultTree';
import { Composer } from './Composer';
import { ThreadList, ThreadMenu } from './ThreadList';
import { Turn } from './Turn';
import { useConversation } from './useConversation';

/** What a page opened at a conversation's address may carry: its first question, to ask. */
export interface Asking { ask: string; k?: number }

/** A conversation begun for a question, and where it is opened to ask it. */
export async function fromAddress(question: string, k: number, scope: string[] | null = null) {
  const thread = await call(api.POST('/v1/threads', { body: { scope } }));
  return { path: `/c/${thread.id}`, state: { ask: question, k } satisfies Asking };
}

/** Where a picked document is: which turn, which row of it. */
interface Picked { turn: number; row: Row }

export function ChatPage({ id }: { id: string }) {
  const t = useT();
  const navigate = useNavigate();
  const location = useLocation();
  const narrow = useMediaQuery('(max-width: 1023px)');
  const [selected, setSelected] = useState<Picked | null>(null);
  /** Picked by the reader -- then a turn arriving does not move the preview. */
  const picked = useRef(false);
  /** How many citations were followed: each opens the preview afresh, on the cited passage. */
  const [cites, setCites] = useState(0);
  const chat = useConversation(id, { openThere: (made) => navigate(`/c/${made}`), onAsk: () => { picked.current = false; } });
  const { thread, map, build, kept, here, stamp, dropped, queued, end, all, latest, last, running, askNow } = chat;

  // The first question an address brought, asked once the conversation and the graph are known.
  const asking = (location.state as Asking | null)?.ask;
  const started = useRef(false);
  useEffect(() => {
    if (!asking || started.current || !kept || map.isPending) return;
    started.current = true;
    const k = (location.state as Asking).k;
    navigate(location.pathname, { replace: true, state: null }); // a reload does not ask again
    askNow(asking, k);
  }, [asking, kept, map.isPending, askNow, navigate, location]);

  // On a wide screen the newest turn's first document is open on the right -- and once its answer
  // is in, the document the answer cites first -- unless the reader has picked one.
  const lastTrail = useMemo(() => hopTrail(last?.found?.sources ?? []), [last?.found]);
  const lastAnswer = last?.answer?.answer;
  useEffect(() => {
    if (narrow || picked.current) return;
    const cited = lastAnswer ? [...citedFiles(lastAnswer)].map((f) => lastTrail.nodes.find((n) => n.label === f)).find(Boolean) : undefined;
    const doc = cited?.id ?? lastTrail.rows[0]?.doc.id;
    const row = doc ? firstRow(lastTrail, doc) : null;
    if (row) setSelected({ turn: latest, row });
  }, [lastTrail, lastAnswer, latest, narrow]);

  const open = useCallback((row: Row, cited: boolean, turn: number) => {
    picked.current = true;
    setSelected({ turn, row });
    if (cited) setCites((n) => n + 1);
  }, []);

  if (thread.isError) {
    return (
      <Empty className="pt-24" icon={<MessagesSquare className="size-5" />} title={t('chatNotFound')}>
        <p>{errorText(thread.error)}</p>
        <Link to="/" className="mt-3 inline-block text-accent-fg hover:underline">{t('newQuestion')}</Link>
      </Empty>
    );
  }

  const groups = [...new Set(dropped.elsewhere.map((e) => e.workspace))];
  const note = (dropped.waiting || queued || groups.length || !here) && (
    <div className="flex flex-col gap-1">
      {dropped.waiting && <span role="status">{build && build.total ? t('scopeBuilding', build.done, build.total) : t('scopePreparing')}{queued && ` · ${t('askWhenReady')}`}</span>}
      {groups.map((ws) => (
        <span key={ws}>
          {t('scopeElsewhere', dropped.elsewhere.filter((e) => e.workspace === ws).length, chat.shownWorkspace(ws))}{' '}
          <button type="button" className="font-medium text-accent-fg hover:underline" onClick={() => dropped.askThere(ws)}>{t('askThere')}</button>
        </span>
      ))}
      {kept && !here && <span>{t('threadWorkspace', chat.shownWorkspace(kept.workspace))}</span>}
    </div>
  );
  const pickedTurn = selected ? all[selected.turn] : undefined;
  const preview = selected && pickedTurn && (
    <Preview key={`${selected.turn}:${selected.row.entry.doc.id}:${cites}`} row={selected.row} question={pickedTurn.question} />
  );

  return (
    <div className="grid h-full grid-cols-1 lg:grid-cols-[220px_minmax(0,1fr)_minmax(380px,42%)]">
      <aside className="hidden min-h-0 flex-col border-r border-line bg-subtle/50 lg:flex" aria-label={t('recent')}>
        <div className="p-3">
          <Link to="/" className="flex h-8 items-center gap-2 rounded-md border border-line bg-panel px-2.5 text-sm font-medium hover:bg-hover">
            <Plus className="size-4" />{t('newQuestion')}
          </Link>
        </div>
        <h2 className="px-4 pb-1 text-2xs font-medium tracking-wide text-faint uppercase">{t('recent')}</h2>
        <ThreadList current={id} />
      </aside>
      <div className="flex min-h-0 flex-col">
        {narrow && <div className="flex items-center gap-2 border-b border-line px-4 py-2"><ThreadMenu /><span className="truncate text-sm font-medium">{kept?.title}</span></div>}
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto flex max-w-3xl flex-col gap-8 px-4 py-4 sm:px-6">
            {!kept ? <div className="space-y-3"><Skeleton className="h-10" /><Skeleton className="h-24" /></div>
              : all.map((turn, i) => (
                <Turn key={i} index={i} turn={turn} stage={chat.stage(i)} now={stamp} busy={running}
                  selected={selected?.turn === i ? selected.row.key : null} onOpenDocument={open}
                  onStop={chat.stop} onAskAgain={askNow} onOnly={chat.only} onWithout={chat.without} />
              ))}
            <div ref={end} />
          </div>
        </div>
        <div className="mx-auto w-full max-w-3xl px-4 pb-4 sm:px-6">
          <Composer
            scope={chat.scope}
            note={note || undefined}
            busy={running}
            autoFocus={!narrow}
            onAsk={chat.askBox}
            onRemove={chat.removeScope}
            onDrop={dropped.drop}
          />
        </div>
      </div>
      {!narrow ? (
        <aside className="hidden min-h-0 border-l border-line bg-panel lg:block" aria-label={t('preview')}>
          {preview || <Empty className="h-full" icon={<FolderSearch className="size-5" />} title={t('previewEmpty')}>{t('previewEmptyBody')}</Empty>}
        </aside>
      ) : (
        <Sheet open={!!preview} onOpenChange={(o) => { if (!o) setSelected(null); }} title={shownName(selected?.row.entry.doc.label ?? '')} closeLabel={t('close')}>
          {preview}
        </Sheet>
      )}
    </div>
  );
}
