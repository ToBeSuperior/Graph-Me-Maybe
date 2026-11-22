// A fingerprint of the graph: its documents, their contents and the links between them. A turn
// keeps the one it was asked of, so a turn read later says when the graph has changed since.
import type { CorpusMap } from '../api/client';

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
