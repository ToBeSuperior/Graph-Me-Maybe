import { QueryClient, QueryObserver } from '@tanstack/react-query';
import { describe, expect, it } from 'vitest';
import { graphReset, keys } from './queries';

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
