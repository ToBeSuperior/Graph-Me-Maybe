// What a question found and the answer written for it, kept for this tab, so reloading the page
// does not ask the model again. Each is kept with a fingerprint of the graph it came from (its
// documents, their contents and the links between them); once the graph differs, it is asked
// again rather than shown stale.
import type { CorpusMap } from '../api/client';

// Named for what the count means: what was kept when it counted passages is not read again.
const KEY = 'kept.documents';
const KEPT = 20;

/** FNV-1a over the graph's shape: which documents (by content), and how they are linked. */
export function graphStamp(map: CorpusMap): string {
  const shape = JSON.stringify([
    map.total,
    map.documents.map((d) => [d.id, d.content_hash]),
    map.links.map((l) => [l.from, l.to, l.count]),
    map.similar.map((l) => [l.from, l.to, l.score]),
  ]);
  let hash = 0x811c9dc5;
  for (let i = 0; i < shape.length; i++) hash = Math.imul(hash ^ shape.charCodeAt(i), 0x01000193) >>> 0;
  return hash.toString(36);
}

interface Entry { key: string; stamp: string; data: unknown }

const read = (): Entry[] => {
  try { return JSON.parse(sessionStorage.getItem(KEY) ?? '[]') as Entry[]; } catch { return []; }
};

const keyOf = (kind: string, question: string, topK: number) => JSON.stringify([kind, question, topK]);

export function kept<T>(kind: 'search' | 'ask', question: string, topK: number, stamp: string): T | undefined {
  return read().find((e) => e.key === keyOf(kind, question, topK) && e.stamp === stamp)?.data as T | undefined;
}

export function keep(kind: 'search' | 'ask', question: string, topK: number, stamp: string, data: unknown) {
  const key = keyOf(kind, question, topK);
  let next = [{ key, stamp, data }, ...read().filter((e) => e.key !== key)].slice(0, KEPT);
  // Storage full: the oldest let go until it fits, rather than nothing kept from then on.
  while (next.length) {
    try { sessionStorage.setItem(KEY, JSON.stringify(next)); return; } catch { next = next.slice(0, next.length >> 1); }
  }
}
