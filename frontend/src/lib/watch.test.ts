import { describe, expect, it } from 'vitest';
import { watching, type WatchTarget } from './watch';

const target = (over: Partial<WatchTarget>): WatchTarget => ({
  kind: 'folder', id: 't', workspace: 'default', mode: 'manual', name: 'Invented', changed: 0, missing: [], building: false, ...over,
});

describe('what is watched', () => {
  it('adds the changes and takes the earliest next build', () => {
    expect(watching([
      target({ changed: 2 }),
      target({ kind: 'drive', mode: 'daily', changed: 1, next_build: '2026-05-05T02:00:00' }),
      target({ kind: 'mail', mode: 'on_change', next_build: '2026-05-04T23:30:00' }),
    ])).toEqual({ changed: 3, next: '23:30', unreadable: 0 });
  });

  it('counts the targets that cannot be read', () => {
    expect(watching([target({ error: 'an invented folder is gone' }), target({})])?.unreadable).toBe(1);
  });

  it('leaves out a mail account left manual, and says nothing when nothing is watched', () => {
    expect(watching([target({ kind: 'mail', changed: 9 })])).toBeNull();
    expect(watching([])).toBeNull();
  });
});
