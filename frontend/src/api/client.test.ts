import { describe, expect, it } from 'vitest';
import { ApiError, call, lastBuild, lastExport, retrying, type Job } from './client';

const build = (id: string, kind: 'build' | 'read' | 'embed', state = 'done') => ({ id, kind, state, done: 0, total: 0,
  source: '', failed: [], documents: 0, known: 0, carried: 0, links: 0, skipped: 0, embedded: 0, looked: 0, error: null }) as Job;
const dump = (id: string) => ({ id, kind: 'dump', state: 'done', message: '', documents: null, finished_at: null,
  dump_ready: false }) as Job;

describe('the newest job in each slot', () => {
  it('takes the first of its slot in a list that is newest first', () => {
    const jobs = [dump('d2'), build('e1', 'embed', 'embedding'), build('b1', 'build'), dump('d1')];
    expect(lastBuild(jobs)?.id).toBe('e1');
    expect(lastExport(jobs)?.id).toBe('d2');
  });
  it('is null before the slot has run anything', () => {
    expect(lastBuild([dump('d1')])).toBeNull();
    expect(lastExport([build('r1', 'read')])).toBeNull();
  });
});

describe('a server that says to wait', () => {
  it('is asked again until it answers; any other failure once', async () => {
    const waiting = await call(Promise.resolve({ error: { detail: 'the graph DB is starting' },
      response: new Response(null, { status: 503, headers: { 'Retry-After': '3' } }) })).catch((e) => e);
    const down = await call(Promise.resolve({ error: { detail: 'no' }, response: new Response(null, { status: 503 }) }))
      .catch((e) => e);
    expect(waiting).toBeInstanceOf(ApiError);
    expect(retrying(50, waiting)).toBe(true);
    expect([retrying(0, down), retrying(1, down)]).toEqual([true, false]);
  });
});
