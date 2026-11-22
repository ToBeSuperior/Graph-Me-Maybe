// What the chosen targets wait to build, said in one line: the agent's menu says the same
// (`graphme.agent.watching_line`). A mail account left manual is not watched.
import type { Schemas } from '../api/client';

export type WatchTarget = Schemas['WatchTarget'];

/** Every watched target's changes, the earliest next build (HH:MM) and how many cannot be read;
 * null when none is watched. */
export function watching(targets: WatchTarget[]): { changed: number; next: string | null; unreadable: number } | null {
  const watched = targets.filter((t) => t.kind !== 'mail' || t.mode !== 'manual');
  if (!watched.length) return null;
  const due = watched.flatMap((t) => (t.next_build ? [t.next_build] : [])).sort();
  return {
    changed: watched.reduce((sum, t) => sum + t.changed, 0),
    next: due.length ? due[0].slice(11, 16) : null,
    unreadable: watched.filter((t) => t.error).length,
  };
}
