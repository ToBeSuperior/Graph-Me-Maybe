import { QueryClient, QueryObserver } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';
import { ApiError } from '../api/client';
import { corpusMapOptions, graphReset, keys, libraryOptions, queryDefaults, workspaceOpened, workspacesOptions } from './queries';

describe('the graph emptied', () => {
  it('keeps the build followed, so the next build is seen to end', () => {
    // Removing the build's answer cut the app-wide follower off it: the samples read after a
    // reset finished unseen, and the map stayed at zero until the app was opened again.
    const client = new QueryClient();
    const follower = new QueryObserver(client, { queryKey: keys.build, queryFn: () => null, enabled: false });
    const seen: unknown[] = [];
    const stop = follower.subscribe((result) => seen.push(result.data));
    graphReset(client);
    client.setQueryData(keys.build, { id: 'b1', state: 'reading' });
    stop();
    expect(seen.at(-1)).toEqual({ id: 'b1', state: 'reading' });
  });
});

describe('the map while the graph DB starts', () => {
  it('is asked again until the server answers, however long that takes', async () => {
    // It gave up after three 503s in six seconds and showed "cannot connect" for good,
    // though the server was ready thirty seconds later.
    vi.useFakeTimers();
    const client = new QueryClient({ defaultOptions: { queries: queryDefaults } });
    let asked = 0;
    const queryFn = () => (++asked < 8 ? Promise.reject(new ApiError('starting', 503, true)) : Promise.resolve('map'));
    const map = new QueryObserver(client, { ...corpusMapOptions, queryFn } as never);
    const stop = map.subscribe(() => {});
    await vi.advanceTimersByTimeAsync(60_000);
    stop();
    vi.useRealTimers();
    expect(map.getCurrentResult().data).toBe('map');
  });

  it('is asked again when the page comes back, for a graph the command line changed', () => {
    expect(corpusMapOptions.refetchOnWindowFocus).toBe('always');
    expect(libraryOptions.refetchOnWindowFocus).toBe('always');
  });
});

describe('the workspaces, changed outside the page', () => {
  it('are asked again when the window comes back and while it shows', () => {
    // The icon's menu turned MCP off; the switch on Keys and model stayed on.
    expect(workspacesOptions.refetchOnWindowFocus).toBe('always');
    expect(workspacesOptions.refetchInterval({ state: { error: null } })).toBeGreaterThan(0);
  });

  it('drop what was drawn from the last graph when another is open', () => {
    // Opened by an MCP client: the list showed the new workspace, the header the old one.
    const client = new QueryClient();
    client.setQueryData(keys.map, 'invented map');
    client.setQueryData(keys.workspaces, 'kept');
    expect(workspaceOpened(client, 'main', 'main')).toBe(false);
    expect(client.getQueryData(keys.map)).toBe('invented map');
    expect(workspaceOpened(client, 'main', 'other')).toBe(true);
    expect(client.getQueryData(keys.map)).toBeUndefined();
    expect(client.getQueryData(keys.workspaces)).toBe('kept');
  });
});
