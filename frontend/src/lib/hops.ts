// What a question found, in documents: the documents of the passages found (hop 1), the
// documents linked to them (hop 2), and the documents linked to those (hop 3).
import type { Source } from '../api/client';

export type Direction = 'cites' | 'cited_by' | 'similar';

export interface Passage { id: string; score: number; members: string[]; text: string }

export interface TrailDoc {
  id: string;
  label: string;
  hop: 1 | 2 | 3;
  rank?: number;
  score?: number;
  modified?: number | null;
  passages: Passage[];
}

export interface TrailEntry { doc: TrailDoc; via?: string; direction?: Direction; next: TrailEntry[] }

export interface Trail {
  /** One per hop-1 document, in the order the answer was given its passages: the order of
   * priority, with the walk out of it. */
  rows: TrailEntry[];
  /** Every document once, at the nearest hop it was reached by. */
  nodes: TrailDoc[];
  /** Each line running the way the walk went, saying how the two are linked. */
  edges: { from: string; to: string; direction: Direction }[];
}

export function hopTrail(sources: Source[]): Trail {
  const nodes = new Map<string, TrailDoc>();
  const edges = new Map<string, Trail['edges'][number]>();
  const rows = new Map<string, TrailEntry>();
  const reach = (doc: TrailDoc) => {
    const known = nodes.get(doc.id);
    if (!known) nodes.set(doc.id, doc);
    else if (known.hop > doc.hop) Object.assign(known, doc);
  };
  for (const hit of sources) {
    if (!rows.has(hit.document_id)) {
      rows.set(hit.document_id, {
        doc: { id: hit.document_id, label: hit.file, hop: 1, rank: rows.size + 1, score: hit.score,
          modified: hit.modified, passages: [] },
        next: [],
      });
    }
    const row = rows.get(hit.document_id)!;
    row.doc.passages.push({ id: hit.id, score: hit.score, members: hit.members, text: hit.context });
    reach({ ...row.doc });
    const second = new Map<string, TrailEntry>();
    for (const doc of hit.linked ?? []) {
      const direction = doc.direction as Direction;
      reach({ id: doc.document_id, label: doc.file, hop: doc.hop as 2 | 3, modified: doc.modified, passages: doc.passages });
      const key = `${doc.via}>${doc.document_id}`;
      if (!edges.has(key)) edges.set(key, { from: doc.via, to: doc.document_id, direction });
      if (doc.hop === 2 && !row.next.some((n) => n.doc.id === doc.document_id)) {
        const entry: TrailEntry = {
          doc: { id: doc.document_id, label: doc.file, hop: 2, modified: doc.modified, passages: doc.passages },
          via: doc.via, direction, next: [],
        };
        row.next.push(entry);
        second.set(doc.document_id, entry);
      }
    }
    for (const doc of (hit.linked ?? []).filter((d) => d.hop === 3)) {
      const entry = second.get(doc.via) ?? row.next.find((n) => n.doc.id === doc.via);
      if (entry && !entry.next.some((t) => t.doc.id === doc.document_id)) {
        entry.next.push({
          doc: { id: doc.document_id, label: doc.file, hop: 3, modified: doc.modified, passages: doc.passages },
          via: doc.via, direction: doc.direction as Direction, next: [],
        });
      }
    }
  }
  // A hop-1 row keeps the passages it gathered: the node copy took them before the rest came.
  for (const row of rows.values()) Object.assign(nodes.get(row.doc.id)!, { passages: row.doc.passages });
  return { rows: [...rows.values()], nodes: [...nodes.values()], edges: [...edges.values()] };
}

/** The files an answer cites: it ends its statements with [file name]. */
export const citedFiles = (answer: string) =>
  new Set([...answer.matchAll(/\[([^[\]]+)\]/g)].map((m) => m[1].trim()));

/** The way to the answer, row by row. The documents the answer cites are looked for along each
 * found document's own walk: the document itself (hop 1), the documents linked to it (hop 2),
 * and theirs (hop 3). Each row gets every walk that reached one (`routes`) and how many hops the
 * shortest took (`reach`, 0 when none did). */
export function answerPath(trail: Trail, answer: string) {
  const cited = citedFiles(answer);
  const used = new Set(trail.nodes.filter((n) => cited.has(n.label)).map((n) => n.id));
  const rows = trail.rows.map(({ doc, next }) => {
    const routes: string[][] = [];
    if (used.has(doc.id)) routes.push([doc.id]);
    for (const second of next) {
      if (used.has(second.doc.id)) routes.push([doc.id, second.doc.id]);
      for (const third of second.next) if (used.has(third.doc.id)) routes.push([doc.id, second.doc.id, third.doc.id]);
    }
    return { id: doc.id, reach: routes.length ? Math.min(...routes.map((r) => r.length)) : 0, routes };
  });
  const lines = (route: string[]) => route.slice(1).map((id, i) => `${route[i]}>${id}`);
  const all = rows.flatMap((r) => r.routes);
  const shortest = all.length ? Math.min(...all.map((r) => r.length)) : 0;
  return {
    used, rows, shortest,
    path: new Set(all.flat()),
    edges: new Set(all.flatMap(lines)),
    shortestEdges: new Set(all.filter((r) => r.length === shortest).flatMap(lines)),
  };
}

/** How many times each document stands in the folders: one shown under several found documents
 * says so, or a reader takes it to be tied only to the first. */
export function placesOf(trail: Trail) {
  const count = new Map<string, number>();
  const add = (id: string) => count.set(id, (count.get(id) ?? 0) + 1);
  for (const row of trail.rows) {
    add(row.doc.id);
    for (const second of row.next) { add(second.doc.id); second.next.forEach((third) => add(third.doc.id)); }
  }
  return count;
}
