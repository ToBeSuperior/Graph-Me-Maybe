// The small window the right-click "Ask" opens (graphme/ask_window.py): a conversation about the
// files picked, narrow -- the found documents' top three, no preview -- above other windows until
// unpinned. Its address names the conversation only; the window hands the page the picked paths,
// which are asked as dropped paths are: files no graph holds built first, showing how far, and
// those another workspace holds offered to be asked there.
import { ExternalLink, MessagesSquare, Pin, PinOff } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { errorText } from '../../api/client';
import { Button, Empty, Skeleton } from '../../components/ui';
import { useT } from '../../lib/i18n';
import { useWorkspaces } from '../../lib/queries';
import { Composer } from './Composer';
import { Turn } from './Turn';
import { useConversation } from './useConversation';

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
  const navigate = useNavigate();
  const location = useLocation();
  const workspaces = useWorkspaces();
  const bridge = useWindowApi();
  const [pinned, setPinned] = useState(true);
  const [opacity, setOpacity] = useState(100);
  useEffect(() => { void bridge?.opacity?.().then((v) => setOpacity(Math.round(v * 100))).catch(() => {}); }, [bridge]);
  const chat = useConversation(id, { openThere: (made) => navigate(`/mini/${made}`, { state: { scoped: true } }) });
  const { thread, build, kept, here, stamp, dropped: scoped, queued, end, all, running, askNow } = chat;

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

  const pin = () => {
    const on = !pinned;
    setPinned(on);
    void bridge?.pin(on);
  };
  const openMain = useCallback(() => {
    if (bridge) void bridge.open_main(id);
    else window.open(`/c/${id}`, '_blank', 'noopener');
  }, [bridge, id]);

  if (thread.isError) {
    return (
      <Empty className="pt-16" icon={<MessagesSquare className="size-5" />} title={t('chatNotFound')}>
        <p>{errorText(thread.error)}</p>
      </Empty>
    );
  }

  const groups = [...new Set(scoped.elsewhere.map((e) => e.workspace))];
  const note = (scoped.waiting || queued || groups.length || !here) && (
    <div className="flex flex-col gap-1">
      {scoped.waiting && <span role="status">{build && build.total ? t('scopeBuilding', build.done, build.total) : t('scopePreparing')}{queued && ` · ${t('askWhenReady')}`}</span>}
      {groups.map((ws) => (
        <span key={ws}>
          {t('scopeElsewhere', scoped.elsewhere.filter((e) => e.workspace === ws).length, chat.shownWorkspace(ws))}{' '}
          <button type="button" className="font-medium text-accent-fg hover:underline" onClick={() => scoped.askThere(ws)}>{t('askThere')}</button>
        </span>
      ))}
      {kept && !here && <span>{t('threadWorkspace', chat.shownWorkspace(kept.workspace))}</span>}
    </div>
  );

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
              <Turn key={i} index={i} turn={turn} stage={chat.stage(i)} now={stamp} compact busy={running}
                onOpenDocument={openMain} onStop={chat.stop} onAskAgain={askNow} />
            ))}
          <div ref={end} />
        </div>
      </div>
      <div className="px-3 pb-3">
        <Composer
          scope={chat.scope}
          note={note || undefined}
          busy={running}
          compact
          autoFocus
          onAsk={chat.askBox}
          onRemove={chat.removeScope}
          onDrop={scoped.drop}
        />
      </div>
    </div>
  );
}
