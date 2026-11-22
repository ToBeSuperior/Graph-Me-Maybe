// A conversation in three panes: the conversations kept, its turns over the box it is asked in,
// and the document picked from a turn on the right. On a narrow screen the conversations are a
// menu and the document a sheet. A conversation asks about its scope -- every document, or those
// chosen in it or dropped on its box -- and keeps it from turn to turn.
import { useQueryClient } from '@tanstack/react-query';
import { FolderSearch, MessagesSquare, Plus } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import { api, call, errorText } from '../../api/client';
import { Empty, Sheet, Skeleton } from '../../components/ui';
import { shownName } from '../../lib/format';
import { citedFiles, hopTrail, type TrailDoc } from '../../lib/hops';
import { useT } from '../../lib/i18n';
import { graphStamp } from '../../lib/kept';
import { keys, useBuild, useCorpusMap, useThread, useWorkspaces } from '../../lib/queries';
import { useMediaQuery } from '../../lib/useMediaQuery';
import { Preview } from '../search/Preview';
import { firstRow, type Row } from '../search/ResultTree';
import { Composer, withDocument, withoutDocument } from './Composer';
import { ThreadList, ThreadMenu } from './ThreadList';
import { Turn, type TurnShape } from './Turn';
import { useScopeDrop } from './useScopeDrop';
import { useTurnStream } from './useTurnStream';

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
  const client = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const thread = useThread(id);
  const map = useCorpusMap();
  const workspaces = useWorkspaces();
  const { data: build } = useBuild();
  const { live, ask, stop } = useTurnStream(id);
  const narrow = useMediaQuery('(max-width: 1023px)');
  const [selected, setSelected] = useState<Picked | null>(null);
  /** Picked by the reader -- then a turn arriving does not move the preview. */
  const picked = useRef(false);
  /** How many citations were followed: each opens the preview afresh, on the cited passage. */
  const [cites, setCites] = useState(0);
  /** A question asked while dropped files are still being built: asked once they are in. */
  const [queued, setQueued] = useState<{ question: string; documents?: number } | null>(null);
  const end = useRef<HTMLDivElement>(null);

  const kept = thread.data;
  const active = workspaces.data?.active ?? kept?.workspace ?? '';
  // A fingerprint is of the graph open: it says nothing of a conversation asking another.
  const here = !!kept && kept.workspace === active;
  const stamp = useMemo(() => (map.data && here ? graphStamp(map.data) : null), [map.data, here]);
  const labels = useMemo(() => new Map(map.data?.documents.map((d) => [d.id, d.label])), [map.data]);

  const askNow = useCallback((question: string, documents?: number, exclude?: string[]) => {
    picked.current = false;
    void ask(question, { documents, stamp, exclude });
  }, [ask, stamp]);

  const setScope = useCallback(async (scope: string[] | null) => {
    try {
      const changed = await call(api.PATCH('/v1/threads/{thread_id}', { params: { path: { thread_id: id } }, body: { scope } }));
      client.setQueryData(keys.thread(id), changed);
    } catch (error) {
      toast.error(t('scopeFailed'), { description: errorText(error) });
      throw error;
    }
  }, [client, id, t]);

  const onScope = useCallback(async (documents: string[], workspace: string) => {
    if (kept && workspace !== kept.workspace) {
      // another workspace holds them: asked there, in a conversation of its own
      const made = await call(api.POST('/v1/threads', { body: { scope: documents, workspace } }));
      void client.invalidateQueries({ queryKey: keys.threads });
      navigate(`/c/${made.id}`);
    } else await setScope(documents);
  }, [client, kept, navigate, setScope]);
  const dropped = useScopeDrop({ active, onScope });

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

  useEffect(() => {
    if (queued && !dropped.waiting) { askNow(queued.question, queued.documents); setQueued(null); }
  }, [queued, dropped.waiting, askNow]);

  const turns: TurnShape[] = kept?.turns ?? [];
  const showLive = !!live && turns.length <= live.after;
  const all = showLive ? [...turns, live] : turns;
  const latest = all.length - 1;
  const last = all[latest];

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

  useEffect(() => { end.current?.scrollIntoView?.({ block: 'end' }); }, [all.length, last?.found, last?.answer]);

  const open = useCallback((turn: number) => (row: Row, cited: boolean) => {
    picked.current = true;
    setSelected({ turn, row });
    if (cited) setCites((n) => n + 1);
  }, []);
  const only = useCallback((doc: TrailDoc) => {
    void setScope(withDocument(kept?.scope ?? null, doc.id)).catch(() => {});
  }, [kept, setScope]);
  // Left out of the question asked again alone: the conversation's scope stays as it is.
  const without = useCallback((question: string) => (doc: TrailDoc) => askNow(question, undefined, [doc.id]), [askNow]);

  if (thread.isError) {
    return (
      <Empty className="pt-24" icon={<MessagesSquare className="size-5" />} title={t('chatNotFound')}>
        <p>{errorText(thread.error)}</p>
        <Link to="/" className="mt-3 inline-block text-accent-fg hover:underline">{t('newQuestion')}</Link>
      </Empty>
    );
  }

  const shownWorkspace = (ws: string) => workspaces.data?.items?.find((i) => i.id === ws)?.name ?? ws;
  const groups = [...new Set(dropped.elsewhere.map((e) => e.workspace))];
  const note = (dropped.waiting || queued || groups.length || !here) && (
    <div className="flex flex-col gap-1">
      {dropped.waiting && <span role="status">{build && build.total ? t('scopeBuilding', build.done, build.total) : t('scopePreparing')}{queued && ` · ${t('askWhenReady')}`}</span>}
      {groups.map((ws) => (
        <span key={ws}>
          {t('scopeElsewhere', dropped.elsewhere.filter((e) => e.workspace === ws).length, shownWorkspace(ws))}{' '}
          <button type="button" className="font-medium text-accent-fg hover:underline" onClick={() => dropped.askThere(ws)}>{t('askThere')}</button>
        </span>
      ))}
      {kept && !here && <span>{t('threadWorkspace', shownWorkspace(kept.workspace))}</span>}
    </div>
  );
  const pickedTurn = selected ? all[selected.turn] : undefined;
  const preview = selected && pickedTurn && (
    <Preview key={`${selected.turn}:${selected.row.entry.doc.id}:${cites}`} row={selected.row} question={pickedTurn.question} />
  );
  const running = showLive && live.stage !== 'stopped' && live.stage !== 'failed';

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
                <Turn key={i} turn={turn} stage={showLive && i === latest ? live.stage : undefined} now={stamp}
                  selected={selected?.turn === i ? selected.row.key : null} onOpenDocument={open(i)}
                  onStop={stop} onAskAgain={askNow} onOnly={only} onWithout={without(turn.question)} />
              ))}
            <div ref={end} />
          </div>
        </div>
        <div className="mx-auto w-full max-w-3xl px-4 pb-4 sm:px-6">
          <Composer
            scope={kept?.scope ? kept.scope.map((d) => ({ id: d, label: labels.get(d) ?? d })) : null}
            note={note || undefined}
            busy={running}
            autoFocus={!narrow}
            onAsk={(question, documents) => (dropped.waiting ? setQueued({ question, documents }) : askNow(question, documents))}
            onRemove={(doc) => {
              void setScope(doc ? withoutDocument(kept?.scope ?? null, doc) : null).catch(() => {});
            }}
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
