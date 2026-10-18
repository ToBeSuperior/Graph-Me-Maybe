import { describe, expect, it } from 'vitest';
import { lastBuild, lastExport, type Job } from './client';

const build = (id: string, kind: 'build' | 'read' | 'embed', state = 'done') => ({ id, kind, state, done: 0, total: 0,
  source: '', failed: [], documents: 0, known: 0, carried: 0, links: 0, skipped: 0, embedded: 0, error: null }) as Job;
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
