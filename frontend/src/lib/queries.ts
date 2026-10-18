// What the app asks the server, in one place: each answer cached under its key, the build
// followed while it runs, and everything that depends on the graph asked again when the graph
// changes (a build ends, a document is deleted, the graph is reset).
import { useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import { api, call, isBuilding, isWaiting, lastBuild, retryDelay, retrying, type BuildState } from '../api/client';

export const keys = {
  build: ['build'] as const,
  library: ['library'] as const,
  map: ['map'] as const,
  // Under the graph it was asked of: a graph changed is a new key, never the old answer.
  search: (question: string, topK: number, stamp: string | null) => ['search', question, topK, stamp] as const,
  ask: (question: string, topK: number, stamp: string | null) => ['ask', question, topK, stamp] as const,
  document: (id: string) => ['document', id] as const,
  pagesOf: (id: string) => ['pagesOf', id] as const,
  sheets: (doc: string) => ['sheets', doc] as const,
  demoGraph: (doc: string) => ['demoGraph', doc] as const,
  boxes: (doc: string) => ['boxes', doc] as const,
  exportJob: ['export'] as const,
  settings: ['settings'] as const,
};

/** The graph changed: what is drawn from it is asked again. A question's search and answer are
 * not: their keys carry the graph's fingerprint, so the new map makes them new questions --
 * asking the old ones again would only buy a model call the page then throws away. */
export function graphChanged(client: QueryClient) {
  for (const key of [keys.library, keys.map, ['document'], ['pagesOf']]) {
    client.invalidateQueries({ queryKey: key });
  }
}

/** The graph emptied: no build to show, and everything drawn from the graph asked again. The
 * build's answer is emptied, not removed: removed, the app-wide follower lost it, and the next
 * build ended unseen -- the map stayed at zero. */
export function graphReset(client: QueryClient) {
  client.setQueryData(keys.build, null);
  graphChanged(client);
}

/** Every query's: an answer kept until the graph changes (`graphChanged` says when); a failure
 * asked again once -- or, while the server says to wait (the graph DB starting), every three
 * seconds until it answers. */
export const queryDefaults = {
  staleTime: Infinity, retry: retrying, retryDelay, refetchOnWindowFocus: false,
  refetchInterval: (query: { state: { error: unknown } }) => (isWaiting(query.state.error) ? 3000 : false),
} as const;

/** What the graph holds is asked again whenever its page is opened or the window comes back:
 * the command line (`graphme build-graph`) changes the graph without the page knowing. */
const fresh = { refetchOnMount: 'always', refetchOnWindowFocus: 'always' } as const;

export const libraryOptions = { queryKey: keys.library, queryFn: () => call(api.GET('/v1/documents')), ...fresh };
export const useLibrary = () => useQuery(libraryOptions);

/** The keys and model the server runs with, what keeps it from running, and whether it has
 * the demo's samples. */
export const useSettings = () =>
  useQuery({ queryKey: keys.settings, queryFn: () => call(api.GET('/v1/settings')), retry: false });

export const corpusMapOptions = { queryKey: keys.map, queryFn: () => call(api.GET('/v1/graph/map')), ...fresh };
export const useCorpusMap = () => useQuery(corpusMapOptions);

/** Where the newest build stands -- null before the first -- asked every two seconds while it
 * runs. */
export const useBuild = () =>
  useQuery({
    queryKey: keys.build,
    queryFn: async () => lastBuild(await call(api.GET('/v1/jobs'))),
    refetchInterval: (q) => (isBuilding(q.state.data) ? 2000 : false),
    retry: false,
  });

/** The build followed, once for the whole app: the graph's answers asked again the moment it
 * ends, and `onEnd` told how it ended. */
export function useBuildFollower(onEnd: (build: BuildState) => void) {
  const client = useQueryClient();
  const query = useBuild();
  const was = useRef<string | undefined>(undefined);
  const ended = useRef(onEnd);
  ended.current = onEnd;
  useEffect(() => {
    const state = query.data?.state;
    if (was.current && isBuilding({ state: was.current } as BuildState) && state && !isBuilding(query.data)) {
      graphChanged(client);
      ended.current(query.data!);
    }
    // While it runs the library grows document by document.
    if (isBuilding(query.data)) client.invalidateQueries({ queryKey: keys.library });
    was.current = state;
  }, [query.data, client]);
}
