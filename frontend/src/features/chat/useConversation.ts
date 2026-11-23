// A conversation as both its pages hold it -- the whole page and the small window: the thread
// and the graph it is asked of, its scope and the files dropped on it, the turn being asked
// merged after the kept ones, and asking -- again, wider, about one document or without it.
import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';
import { api, call, errorText } from '../../api/client';
import type { TrailDoc } from '../../lib/hops';
import { useT } from '../../lib/i18n';
import { graphStamp } from '../../lib/kept';
import { keys, useBuild, useCorpusMap, useThread, useWorkspaces } from '../../lib/queries';
import { withDocument, withoutDocument } from './Composer';
import type { TurnShape } from './Turn';
import { useScopeDrop } from './useScopeDrop';
import { useTurnStream } from './useTurnStream';

export function useConversation(id: string, { openThere, onAsk }: {
  /** A conversation begun in another workspace for the files it holds: opened. */
  openThere: (thread: string) => void;
  /** Said before each question is asked. */
  onAsk?: () => void;
}) {
  const t = useT();
  const client = useQueryClient();
  const thread = useThread(id);
  const map = useCorpusMap();
  const workspaces = useWorkspaces();
  const { data: build } = useBuild();
  const { live, ask, stop } = useTurnStream(id);
  /** A question asked while dropped files are still being built: asked once they are in. */
  const [queued, setQueued] = useState<{ question: string; documents?: number } | null>(null);
  const end = useRef<HTMLDivElement>(null);
  // the page's callbacks, read when used: a new one each render does not change what is handed down
  const said = useRef({ openThere, onAsk });
  said.current = { openThere, onAsk };

  const kept = thread.data;
  const active = workspaces.data?.active ?? kept?.workspace ?? '';
  // A fingerprint is of the graph open: it says nothing of a conversation asking another.
  const here = !!kept && kept.workspace === active;
  const stamp = useMemo(() => (map.data && here ? graphStamp(map.data) : null), [map.data, here]);
  const labels = useMemo(() => new Map(map.data?.documents.map((d) => [d.id, d.label])), [map.data]);

  const askNow = useCallback((question: string, documents?: number, exclude?: string[]) => {
    said.current.onAsk?.();
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
      said.current.openThere(made.id);
    } else await setScope(documents);
  }, [client, kept, setScope]);
  const dropped = useScopeDrop({ active, onScope });

  useEffect(() => {
    if (queued && !dropped.waiting) { askNow(queued.question, queued.documents); setQueued(null); }
  }, [queued, dropped.waiting, askNow]);

  const turns: TurnShape[] = kept?.turns ?? [];
  const showLive = !!live && turns.length <= live.after;
  const all = showLive ? [...turns, live] : turns;
  const latest = all.length - 1;
  const last = all[latest];
  const running = showLive && live.stage !== 'stopped' && live.stage !== 'failed';
  useEffect(() => { end.current?.scrollIntoView?.({ block: 'end' }); }, [all.length, last?.found, last?.answer]);

  const only = useCallback((doc: TrailDoc) => {
    void setScope(withDocument(kept?.scope ?? null, doc.id)).catch(() => {});
  }, [kept, setScope]);
  // Left out of the question asked again alone: the conversation's scope stays as it is.
  const without = useCallback((doc: TrailDoc, question: string) => askNow(question, undefined, [doc.id]), [askNow]);
  /** A question from the box: held while dropped files are built. */
  const askBox = (question: string, documents?: number) => (dropped.waiting ? setQueued({ question, documents }) : askNow(question, documents));
  /** A scope chip taken off; none named, every document. */
  const removeScope = (doc: string | null) => { void setScope(doc ? withoutDocument(kept?.scope ?? null, doc) : null).catch(() => {}); };
  const shownWorkspace = (ws: string) => workspaces.data?.items?.find((i) => i.id === ws)?.name ?? ws;
  const scope = kept?.scope ? kept.scope.map((d) => ({ id: d, label: labels.get(d) ?? d })) : null;

  return {
    thread, map, build, kept, here, stamp, dropped, queued, end,
    live, showLive, all, latest, last, running, stage: (i: number) => (showLive && i === latest ? live.stage : undefined),
    askNow, askBox, stop, only, without, removeScope, shownWorkspace, scope,
  };
}
