// A question's trail in documents, and the way from it to what the answer cites.
import { describe, expect, it } from 'vitest';
import type { Source } from '../api/client';
import { answerPath, hopTrail, placesOf } from './hops';

const sources = [
  { id: 'm:0', file: 'memo.docx', score: 0.9, document_id: 'm', members: ['m1'], context: 'memo', linked: [
    { document_id: 'q', file: 'spec.docx', hop: 2, via: 'm', direction: 'cites', passages: [{ id: 'q:0', score: 0.8, members: ['q1'], text: 'rule' }] },
    { document_id: 'k', file: 'ks.docx', hop: 3, via: 'q', direction: 'cites', passages: [{ id: 'k:0', score: 0.6, members: ['k1'], text: 'ks' }] },
    { document_id: 'p', file: 'paper.pdf', hop: 3, via: 'q', direction: 'cited_by', passages: [] }] },
  { id: 'm:4', file: 'memo.docx', score: 0.8, document_id: 'm', members: ['m9'], context: 'memo 2', linked: [] },
  { id: 'p:3', file: 'paper.pdf', score: 0.6, document_id: 'p', members: ['p9'], context: 'x', linked: [
    { document_id: 'q', file: 'spec.docx', hop: 2, via: 'p', direction: 'cites', passages: [] }] },
] as Source[];

describe('hopTrail', () => {
  const trail = hopTrail(sources);
  it('makes one row per found document, in order, with its walk', () => {
    expect(trail.rows.map((r) => r.doc.id)).toEqual(['m', 'p']);
    expect(trail.rows[0].doc.passages.map((x) => x.id)).toEqual(['m:0', 'm:4']);
    expect(trail.rows[0].next.map((n) => [n.doc.id, n.next.map((t) => t.doc.id)])).toEqual([['q', ['k', 'p']]]);
  });
  it('keeps each document once, at the nearest hop', () => {
    expect(Object.fromEntries(trail.nodes.map((n) => [n.id, n.hop]))).toEqual({ m: 1, q: 2, k: 3, p: 1 });
    expect(trail.nodes.find((n) => n.id === 'p')!.rank).toBe(2);
    expect(trail.nodes.find((n) => n.id === 'm')!.passages).toHaveLength(2);
    expect(trail.edges.map((e) => `${e.from}>${e.to}:${e.direction}`).sort())
      .toEqual(['m>q:cites', 'p>q:cites', 'q>k:cites', 'q>p:cited_by']);
    expect(hopTrail([])).toEqual({ rows: [], nodes: [], edges: [] });
  });
  it('counts where a document stands more than once', () => {
    const places = placesOf(trail);
    expect(places.get('q')).toBe(2);
    expect(places.get('p')).toBe(2);
    expect(places.get('m')).toBe(1);
  });
});

describe('answerPath', () => {
  const trail = hopTrail(sources);
  it('finds the walk to a cited document and its length', () => {
    const way = answerPath(trail, '규칙은 이렇다 [ks.docx]. 또한 [없는 파일.pdf]은 무시된다.');
    expect([...way.used]).toEqual(['k']);
    expect(way.rows.map((r) => [r.id, r.reach, r.routes])).toEqual([['m', 3, [['m', 'q', 'k']]], ['p', 0, []]]);
    expect([...way.edges].sort()).toEqual(['m>q', 'q>k']);
    expect(way.shortest).toBe(3);
  });
  it('reaches from two found documents, or at hop 1, or not at all', () => {
    expect(answerPath(trail, '[spec.docx]').rows.map((r) => [r.id, r.reach])).toEqual([['m', 2], ['p', 2]]);
    const own = answerPath(trail, '[memo.docx]');
    expect(own.rows.map((r) => [r.id, r.reach])).toEqual([['m', 1], ['p', 0]]);
    expect(own.edges.size).toBe(0);
    const none = answerPath(trail, '근거를 찾을 수 없습니다.');
    expect(none.used.size).toBe(0);
    expect(none.rows.every((r) => r.reach === 0)).toBe(true);
  });
});
