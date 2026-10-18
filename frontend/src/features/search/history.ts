// The questions of this tab, kept while it is open: the last ten, as questions only. What a
// question found is asked again when it is opened -- the graph may have changed since, and a
// list that showed what it found then would show what is no longer so.
const KEY = 'questions';
const KEPT = 10;

export interface Asked { question: string; topK: number }

export function asked(): Asked[] {
  try { return JSON.parse(sessionStorage.getItem(KEY) ?? '[]') as Asked[]; } catch { return []; }
}

/** A new question goes on top; one already there stays where it is -- opened from the list, it
 * would otherwise jump out from under the pointer. */
export function remember(entry: Asked): Asked[] {
  const before = asked();
  const next = before.some((a) => a.question === entry.question)
    ? before.map((a) => (a.question === entry.question ? entry : a))
    : [entry, ...before].slice(0, KEPT);
  try { sessionStorage.setItem(KEY, JSON.stringify(next)); } catch { /* storage off */ }
  return next;
}

export function forget(question: string): Asked[] {
  const next = asked().filter((a) => a.question !== question);
  try { sessionStorage.setItem(KEY, JSON.stringify(next)); } catch { /* storage off */ }
  return next;
}
