// The small window the right-click "Ask" opens (graphme/ask_window.py): a conversation about the
// files picked, narrow -- the found documents' top three, no preview -- above other windows until
// unpinned. Its address names the conversation only; the window hands the page the picked paths,
// which are asked as dropped paths are: files no graph holds built first, showing how far, and
// those another workspace holds offered to be asked there.
import { useQueryClient } from '@tanstack/react-query';
import { ExternalLink, MessagesSquare, Pin, PinOff } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import { api, call, errorText } from '../../api/client';
import { Button, Empty, Skeleton } from '../../components/ui';
import { useT } from '../../lib/i18n';
import { graphStamp } from '../../lib/kept';
import { keys, useBuild, useCorpusMap, useThread, useWorkspaces } from '../../lib/queries';
import { Composer, withoutDocument } from './Composer';
import { Turn, type TurnShape } from './Turn';
import { useScopeDrop } from './useScopeDrop';
import { useTurnStream } from './useTurnStream';

/** What the small window lets its page ask of it (`MiniApi`); none in a browser. */
export interface MiniApi {
  paths(): Promise<string[]>;
  pin(on: boolean): Promise<void>;
  /** The whole window this opaque, 0.4 to 1; kept for the next time it opens. */
  set_opacity(value: number): Promise<void>;
  opacity(): Promise<number>;
  open_main(threadId: string): Promise<void>;
}

const windowApi = (): MiniApi | undefined =>
  (window as unknown as { pywebview?: { api?: Partial<MiniApi> } }).pywebview?.api?.paths
    ? (window as unknown as { pywebview: { api: MiniApi } }).pywebview.api : undefined;

/** The window's bridge once it is ready: pywebview hands it over after the page has loaded. */
function useWindowApi(): MiniApi | undefined {
  const [bridge, setBridge] = useState(windowApi);
  useEffect(() => {
    const ready = () => setBridge(windowApi());
    window.addEventListener('pywebviewready', ready);
    return () => window.removeEventListener('pywebviewready', ready);
  }, []);
  return bridge;
}

export function MiniPage({ id }: { id: string }) {
  const t = useT();
  const client = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const thread = useThread(id);
  const map = useCorpusMap();
  const workspaces = useWorkspaces();
  const { data: build } = useBuild();
  const { live, ask, stop } = useTurnStream(id);
  const bridge = useWindowApi();
  const [pinned, setPinned] = useState(true);
  const [opacity, setOpacity] = useState(100);
  useEffect(() => { void bridge?.opacity?.().then((v) => setOpacity(Math.round(v * 100))).catch(() => {}); }, [bridge]);
  const [queued, setQueued] = useState<{ question: string; documents?: number } | null>(null);
  const end = useRef<HTMLDivElement>(null);

  const kept = thread.data;
  const active = workspaces.data?.active ?? kept?.workspace ?? '';
  const here = !!kept && kept.workspace === active;
  const stamp = useMemo(() => (map.data && here ? graphStamp(map.data) : null), [map.data, here]);
  const labels = useMemo(() => new Map(map.data?.documents.map((d) => [d.id, d.label])), [map.data]);

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
      navigate(`/mini/${made.id}`, { state: { scoped: true } });
    } else await setScope(documents);
  }, [kept, navigate, setScope]);
  const scoped = useScopeDrop({ active, onScope });

  // The picked files, asked once the conversation and the workspaces are known: built first
  // when no graph holds them. Only the first conversation does it -- one begun for the files
  // another workspace holds has them already, and asking again would lead back.
  const started = useRef((location.state as { scoped?: boolean } | null)?.scoped ?? false);
  const { askPaths } = scoped;
  useEffect(() => {
    if (started.current || !bridge || !kept || !workspaces.data || kept.turns?.length) return;
    started.current = true;
    void bridge.paths().then((paths) => (paths.length ? askPaths(paths) : undefined));
  }, [bridge, kept, workspaces.data, askPaths]);

  const askNow = useCallback((question: string, documents?: number, exclude?: string[]) => {
    void ask(question, { documents, stamp, exclude });
  }, [ask, stamp]);
  useEffect(() => {
    if (queued && !scoped.waiting) { askNow(queued.question, queued.documents); setQueued(null); }
  }, [queued, scoped.waiting, askNow]);

  const turns: TurnShape[] = kept?.turns ?? [];
  const showLive = !!live && turns.length <= live.after;
  const all = showLive ? [...turns, live] : turns;
  const latest = all.length - 1;
  const last = all[latest];
  useEffect(() => { end.current?.scrollIntoView?.({ block: 'end' }); }, [all.length, last?.found, last?.answer]);

  const pin = () => {
    const on = !pinned;
    setPinned(on);
    void bridge?.pin(on);
  };
  const openMain = () => {
    if (bridge) void bridge.open_main(id);
    else window.open(`/c/${id}`, '_blank', 'noopener');
  };

  if (thread.isError) {
    return (
      <Empty className="pt-16" icon={<MessagesSquare className="size-5" />} title={t('chatNotFound')}>
        <p>{errorText(thread.error)}</p>
      </Empty>
    );
  }

  const shownWorkspace = (ws: string) => workspaces.data?.items?.find((i) => i.id === ws)?.name ?? ws;
  const groups = [...new Set(scoped.elsewhere.map((e) => e.workspace))];
  const note = (scoped.waiting || queued || groups.length || !here) && (
    <div className="flex flex-col gap-1">
      {scoped.waiting && <span role="status">{build && build.total ? t('scopeBuilding', build.done, build.total) : t('scopePreparing')}{queued && ` · ${t('askWhenReady')}`}</span>}
      {groups.map((ws) => (
        <span key={ws}>
          {t('scopeElsewhere', scoped.elsewhere.filter((e) => e.workspace === ws).length, shownWorkspace(ws))}{' '}
          <button type="button" className="font-medium text-accent-fg hover:underline" onClick={() => scoped.askThere(ws)}>{t('askThere')}</button>
        </span>
      ))}
      {kept && !here && <span>{t('threadWorkspace', shownWorkspace(kept.workspace))}</span>}
    </div>
  );
  const running = showLive && live.stage !== 'stopped' && live.stage !== 'failed';

  return (
    <div className="flex h-dvh flex-col bg-bg text-fg">
      <header className="flex items-center gap-1 border-b border-line px-3 py-1.5">
        <span className="min-w-0 flex-1 truncate text-sm font-medium">{kept?.title || 'Graph-Me-Maybe'}</span>
        {bridge && (
          <Button size="sm" variant="ghost" aria-pressed={pinned} aria-label={t('pinOnTop')} title={t('pinOnTop')} onClick={pin}>
            {pinned ? <Pin className="size-3.5" /> : <PinOff className="size-3.5" />}
          </Button>
        )}
        {bridge && (
          <input type="range" min={40} max={100} step={5} value={opacity} aria-label={t('windowOpacity')} title={`${t('windowOpacity')} ${opacity}%`}
            className="w-20 accent-[var(--accent)]"
            onChange={(e) => { const v = Number(e.target.value); setOpacity(v); void bridge.set_opacity(v / 100); }} />
        )}
        <Button size="sm" variant="ghost" aria-label={t('openInMain')} title={t('openInMain')} onClick={openMain}>
          <ExternalLink className="size-3.5" />
        </Button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="flex flex-col gap-6 px-3 py-3">
          {!kept ? <div className="space-y-3"><Skeleton className="h-10" /><Skeleton className="h-24" /></div>
            : all.map((turn, i) => (
              <Turn key={i} turn={turn} stage={showLive && i === latest ? live.stage : undefined} now={stamp} compact
                onOpenDocument={openMain} onStop={stop} onAskAgain={askNow} />
            ))}
          <div ref={end} />
        </div>
      </div>
      <div className="px-3 pb-3">
        <Composer
          scope={kept?.scope ? kept.scope.map((d) => ({ id: d, label: labels.get(d) ?? d })) : null}
          note={note || undefined}
          busy={running}
          compact
          autoFocus
          onAsk={(question, documents) => (scoped.waiting ? setQueued({ question, documents }) : askNow(question, documents))}
          onRemove={(doc) => { void setScope(doc ? withoutDocument(kept?.scope ?? null, doc) : null).catch(() => {}); }}
          onDrop={scoped.drop}
        />
      </div>
    </div>
  );
}
