// The outline's folding, rings, tree and passage spans, checked without a browser.
import { describe, expect, it } from 'vitest';
import { FAN_LIMIT, foldTree, radialLayout, sizeAt, treeLayout, treeSpan } from './graphLayout';

/** A seeded outline: a document, sections, items, a long bibliography-like fan, deep chains. */
function outline(seed: number) {
  let s = seed;
  const rand = () => (s = (s * 1103515245 + 12345) % 2147483648) / 2147483648;
  const parent = new Map<string, string>();
  const ids = ['doc'];
  let next = 0;
  const add = (p: string) => { const id = `n${next++}`; parent.set(id, p); ids.push(id); return id; };
  for (let i = 0; i < 12; i++) add('doc');
  const sections = Array.from({ length: 6 }, () => add('doc'));
  sections.forEach((sec) => {
    const items = Array.from({ length: 2 + Math.floor(rand() * 8) }, () => add(sec));
    items.forEach((item) => { let at = item; for (let d = 0; d < Math.floor(rand() * 4); d++) at = add(at); });
  });
  const refs = add(sections[0]);
  for (let i = 0; i < 55; i++) add(refs);
  return { ids, parentOf: (id: string) => parent.get(id), refs };
}

const { ids, parentOf, refs } = outline(7);
const kidsCount = (id: string) => ids.filter((x) => parentOf(x) === id).length;
const under = (id: string): number => ids.filter((x) => parentOf(x) === id).reduce((n, k) => n + 1 + under(k), 0);

describe('folding', () => {
  it('folds a fan past the limit and counts what it hides', () => {
    expect(kidsCount(refs)).toBeGreaterThan(FAN_LIMIT);
    let fold = foldTree(ids, parentOf, new Map());
    expect(fold.folded.get(refs)).toBe(under(refs));
    expect(fold.shown.length).toBe(ids.length - under(refs));
    fold = foldTree(ids, parentOf, new Map([[refs, false]]));
    expect(fold.shown.length).toBe(ids.length);
  });
  it('never folds a root by default, and folds outermost first', () => {
    const wide = ['root', ...Array.from({ length: 30 }, (_, i) => `w${i}`)];
    const p = (id: string) => (id === 'root' ? undefined : 'root');
    expect(foldTree(wide, p, new Map()).shown.length).toBe(wide.length);
    expect(foldTree(wide, p, new Map([['root', true]])).shown.length).toBe(1);
    const sec = parentOf(refs)!;
    const fold = foldTree(ids, parentOf, new Map([[sec, true], [refs, false]]));
    expect(fold.folded.get(sec)).toBe(under(sec));
    expect(fold.folded.has(refs)).toBe(false);
  });
});

describe('rings', () => {
  it('puts every node on its depth ring, none overlapping, siblings side by side', () => {
    for (const folding of [new Map<string, boolean>(), new Map([[refs, false]])]) {
      const shown = foldTree(ids, parentOf, folding).shown;
      const { position, radius } = radialLayout(shown, parentOf, (id) => id === 'doc');
      expect(position.size).toBe(shown.length);
      const depthOf = (id: string): number => (id === 'doc' ? 0 : depthOf(parentOf(id)!) + 1);
      for (const id of shown) {
        const p = position.get(id)!;
        expect(p.depth).toBe(depthOf(id));
        expect(Math.abs(Math.hypot(p.x, p.y) - radius[p.depth])).toBeLessThan(1e-6);
      }
      for (let i = 0; i < shown.length; i++) for (let j = i + 1; j < shown.length; j++) {
        const a = position.get(shown[i])!; const b = position.get(shown[j])!;
        expect(Math.hypot(a.x - b.x, a.y - b.y)).toBeGreaterThanOrEqual(sizeAt(a.depth) + sizeAt(b.depth) - 1e-6);
      }
      const rings = new Map<number, string[]>();
      shown.forEach((id) => { const d = position.get(id)!.depth; rings.set(d, [...(rings.get(d) ?? []), id]); });
      rings.forEach((ring) => {
        ring.sort((a, b) => position.get(a)!.angle - position.get(b)!.angle);
        const seen = new Set<string | undefined>();
        let last: string | undefined;
        ring.forEach((id) => {
          const p = parentOf(id);
          if (p !== last) { expect(seen.has(p)).toBe(false); seen.add(p); last = p; }
        });
      });
    }
  });
  it('lays out nothing, and a lone document at the centre', () => {
    expect(radialLayout([], parentOf, () => true).position.size).toBe(0);
    const lone = radialLayout(['doc'], () => undefined, () => true).position.get('doc')!;
    expect(lone.depth).toBe(0);
    expect(Math.abs(lone.x) + Math.abs(lone.y)).toBeLessThan(1e-9);
  });
});

describe('spans and tree', () => {
  const spanTree: Record<string, string | undefined> = { a: 'r', b: 'a', c: 'a', d: 'r', e: 'd', x: undefined, y: 'x' };
  const key = (edges: [string, string][]) => edges.map(([c, p]) => `${c}>${p}`).sort();
  it('runs a passage span along the outline', () => {
    const p = (id: string) => spanTree[id];
    expect(key(treeSpan(['b', 'c'], p))).toEqual(['b>a', 'c>a']);
    expect(key(treeSpan(['b', 'e'], p))).toEqual(['a>r', 'b>a', 'd>r', 'e>d']);
    expect(key(treeSpan(['a', 'b'], p))).toEqual(['b>a']);
    expect(treeSpan(['b'], p)).toEqual([]);
    expect(treeSpan([], p)).toEqual([]);
    expect(key(treeSpan(['b', 'y'], p))).toEqual(['a>r', 'b>a', 'y>x']);
  });
  it('keeps the tree in reading order', () => {
    const treeOf: Record<string, string | undefined> = { t: undefined, c3: 't', c1: 't', c2: 't', c2a: 'c2', c2b: 'c2' };
    const placed = treeLayout(['t', 'c1', 'c2', 'c2a', 'c2b', 'c3'], (id) => treeOf[id]);
    const xs = ['c1', 'c2', 'c3'].map((id) => placed.get(id)!.x);
    expect(xs[0]).toBeLessThan(xs[1]);
    expect(xs[1]).toBeLessThan(xs[2]);
    expect(placed.get('c2')!.x).toBe((placed.get('c2a')!.x + placed.get('c2b')!.x) / 2);
    expect(placed.get('t')!.y).toBeLessThan(placed.get('c1')!.y);
  });
});
