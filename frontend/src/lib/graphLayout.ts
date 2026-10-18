// Where the outline's nodes go, apart from how they are drawn: which are folded away, where
// each one stands on the rings or in the tree, and which outline edges a passage's span runs
// along. Nothing here touches the page or vis, so it is checked on its own (graphLayout.test.ts).

type ParentOf = (id: string) => string | undefined;

// Every node is one size -- the rings say the level -- and only the far rings are smaller.
// Small beside the gap between rings, so the rings and the levels they name stay in view.
export const NODE_SIZE = 9;
export const sizeAt = (depth: number) => (depth >= 6 ? 5 : depth === 5 ? 7 : NODE_SIZE);
export const RING_STEP = 72;
/** A node holding more items than this starts folded: one fan otherwise sets every ring. */
export const FAN_LIMIT = 20;
const GAP = 12;

/** Each shown node's items among the shown, in the order given, and the nodes no shown node holds. */
function childrenOf(ids: string[], parentOf: ParentOf) {
  const shown = new Set(ids);
  const kids = new Map<string, string[]>();
  const roots: string[] = [];
  for (const id of ids) {
    const p = parentOf(id);
    if (p !== undefined && shown.has(p)) {
      if (!kids.has(p)) kids.set(p, []);
      kids.get(p)!.push(id);
    } else roots.push(id);
  }
  return { kids, roots };
}

/** What stays drawn once folded nodes take their items with them. `folding` says, per node,
 * whether it was folded (true) or opened (false); a node it does not name is folded when it
 * holds more than FAN_LIMIT. Folding goes outermost first: a node inside a folded one goes with
 * it and is not counted as folded itself. `folded` maps each folded node to how many nodes it
 * holds, however deep. */
export function foldTree(ids: string[], parentOf: ParentOf, folding: Map<string, boolean>) {
  const { kids, roots } = childrenOf(ids, parentOf);
  const gone = new Set<string>();
  const folded = new Map<string, number>();
  const hide = (id: string): void => (kids.get(id) ?? []).forEach((k) => { gone.add(k); hide(k); });
  const under = (id: string): number => (kids.get(id) ?? []).reduce((sum, k) => sum + 1 + under(k), 0);
  // A root is folded only when asked: folded by default, a document would be one node.
  const visit = (id: string, root: boolean): void => {
    const closed = kids.has(id) && (folding.has(id) ? folding.get(id)! : !root && kids.get(id)!.length > FAN_LIMIT);
    if (closed) { hide(id); folded.set(id, under(id)); return; }
    (kids.get(id) ?? []).forEach((k) => visit(k, false));
  };
  roots.forEach((id) => visit(id, true));
  return { shown: ids.filter((id) => !gone.has(id)), folded, holds: (id: string) => kids.has(id) };
}

interface RingPosition { x: number; y: number; depth: number; angle: number }

/** A radial tree: the centre node in the middle, each level on a ring of its own. Every node
 * owns a wedge and its items are laid side by side inside it -- never crossing into a
 * neighbour's, never overlapping. A wedge is as wide as the node needs on its own ring or as
 * its items need together, whichever is more; when the wedges do not fit around the circle,
 * every ring is pushed out until they do. `isCentre` picks the centre among the roots (the
 * document); any other root hangs off it. */
export function radialLayout(ids: string[], parentOf: ParentOf, isCentre: (id: string) => boolean) {
  const { kids, roots } = childrenOf(ids, parentOf);
  if (!roots.length) return { position: new Map<string, RingPosition>(), radius: [0] };
  const size = new Map<string, number>();
  const count = (id: string): number => {
    if (!size.has(id)) size.set(id, (kids.get(id) ?? []).reduce((sum, k) => sum + 1 + count(k), 0));
    return size.get(id)!;
  };
  const centre = roots.find(isCentre) ?? [...roots].sort((a, b) => count(b) - count(a))[0];
  const others = roots.filter((id) => id !== centre);
  if (others.length) kids.set(centre, [...(kids.get(centre) ?? []), ...others]);

  const depth = new Map<string, number>();
  const walk = (id: string, d: number): void => { depth.set(id, d); (kids.get(id) ?? []).forEach((k) => walk(k, d + 1)); };
  walk(centre, 0);
  const deepest = Math.max(...depth.values());
  let radius = Array.from({ length: deepest + 1 }, (_, d) => d * RING_STEP);
  const needs = () => {
    const need = new Map<string, number>();
    const of = (id: string): number => {
      const d = depth.get(id)!;
      const own = d ? (2 * sizeAt(d) + GAP) / radius[d] : 0;
      const together = (kids.get(id) ?? []).reduce((sum, k) => sum + of(k), 0);
      need.set(id, Math.max(own, together));
      return need.get(id)!;
    };
    of(centre);
    return need;
  };
  let need = needs();
  for (let pass = 0; pass < 8 && need.get(centre)! > 2 * Math.PI; pass++) {
    radius = radius.map((r) => (r * need.get(centre)!) / (2 * Math.PI));
    need = needs();
  }

  const angle = new Map<string, number>();
  const place = (id: string, from: number, to: number): void => {
    angle.set(id, (from + to) / 2);
    const items = kids.get(id) ?? [];
    const total = items.reduce((sum, k) => sum + need.get(k)!, 0);
    let at = from;
    for (const k of items) {
      const span = ((to - from) * need.get(k)!) / total;
      place(k, at, at + span);
      at += span;
    }
  };
  place(centre, 0, 2 * Math.PI);

  const position = new Map<string, RingPosition>();
  depth.forEach((d, id) => {
    const a = angle.get(id)! - Math.PI / 2;
    position.set(id, { x: radius[d] * Math.cos(a), y: radius[d] * Math.sin(a), depth: d, angle: angle.get(id)! });
  });
  return { position, radius };
}

/** The outline as a tree, top down, in the order it reads: each node's children left to right in
 * the order given, a leaf one slot to the right of the last, and a parent over the middle of its
 * children. Laid out here rather than by vis, which orders siblings its own way. */
const TREE_STEP = { x: 70, y: 120 };
export function treeLayout(ids: string[], parentOf: ParentOf) {
  const { kids, roots } = childrenOf(ids, parentOf);
  const placed = new Map<string, { x: number; y: number }>();
  let slot = 0;
  const place = (id: string, depth: number): void => {
    const children = kids.get(id) ?? [];
    children.forEach((child) => place(child, depth + 1));
    const x = children.length
      ? (placed.get(children[0])!.x + placed.get(children[children.length - 1])!.x) / 2
      : slot++ * TREE_STEP.x;
    placed.set(id, { x, y: depth * TREE_STEP.y });
  };
  roots.forEach((root) => { place(root, 0); slot++; });
  return placed;
}

/** The edges a chunk's span runs along: the outline's own lines joining the nodes it covers, by
 * the shortest way through the tree -- up from each to the deepest node above them all, or to its
 * root where they share none. As [child, parent] pairs, each once; a node on the way that the
 * chunk does not cover is passed through, not taken in. */
export function treeSpan(ids: string[], parentOf: ParentOf): [string, string][] {
  const up = (id: string) => {
    const path = [id];
    const seen = new Set(path);
    for (let p = parentOf(id); p !== undefined && !seen.has(p); p = parentOf(p)) { path.push(p); seen.add(p); }
    return path;
  };
  const paths = ids.map(up);
  const top = paths.length ? paths[0].find((x) => paths.every((path) => path.includes(x))) : undefined;
  const edges = new Map<string, [string, string]>();
  for (const path of paths) {
    for (let i = 0; i < path.length - 1 && path[i] !== top; i++) edges.set(path[i], [path[i], path[i + 1]]);
  }
  return [...edges.values()];
}
