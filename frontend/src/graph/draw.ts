// The outline as drawn, apart from the component that shows it: what stays drawn once folding is
// applied and where each node goes, the rings behind the nodes, and each passage's span.
import type { DataSet, Edge, Node } from 'vis-network/standalone';
import type { DocumentGraph } from '../api/client';
import { clip, shownName } from '../lib/format';
import { foldTree, NODE_SIZE, radialLayout, RING_STEP, sizeAt, treeLayout, treeSpan } from '../lib/graphLayout';
import type { T } from '../lib/i18n';
import { token } from '../lib/theme';
import { colorOf, edgeStyle, FONT, neoEdge, neoNode, shade, tip } from './look';
import { hiddenBy, hostOf, kindOf, labelOf, levels, type Outline } from './outline';

export type Layout = 'radial' | 'tree' | 'force';
export const CHUNK_PAD = 16;

export function band(ctx: CanvasRenderingContext2D, a: { x: number; y: number }, b: { x: number; y: number }, half: number) {
  const len = Math.hypot(b.x - a.x, b.y - a.y) || 1;
  const nx = (-(b.y - a.y) / len) * half;
  const ny = ((b.x - a.x) / len) * half;
  let corners = [[a.x + nx, a.y + ny], [b.x + nx, b.y + ny], [b.x - nx, b.y - ny], [a.x - nx, a.y - ny]];
  // Wound the way the discs are, so the shapes join instead of cutting holes in each other.
  const area = corners.reduce((sum, [x, y], i) => { const [x2, y2] = corners[(i + 1) % 4]; return sum + x * y2 - x2 * y; }, 0);
  if (area < 0) corners = corners.reverse();
  ctx.moveTo(corners[0][0], corners[0][1]);
  corners.slice(1).forEach(([x, y]) => ctx.lineTo(x, y));
  ctx.closePath();
}

/** What is drawn once folding is applied: the shown nodes, how much each folded one holds, and
 * where each goes in the chosen layout. */
export function drawing(outline: Outline, folding: Map<string, boolean>, showExternal: boolean, layout: Layout) {
  const hidden = hiddenBy(outline, folding, showExternal);
  const inside = new Map<string, number>();
  hidden.forEach((id) => {
    const h = hostOf(outline, outline.byId.get(id)!);
    if (h) inside.set(h, (inside.get(h) ?? 0) + 1);
  });
  const hasParts = new Set(outline.graph.nodes.map((n) => hostOf(outline, n)).filter(Boolean) as string[]);
  const fold = foldTree(outline.graph.nodes.filter((n) => !hidden.has(n.id)).map((n) => n.id), outline.parent, folding);
  const held = (id: string) => (fold.folded.get(id) ?? 0) + (inside.get(id) ?? 0);
  const canFold = (id: string) => fold.holds(id) || hasParts.has(id);
  const rings = layout === 'radial'
    ? radialLayout(fold.shown, outline.parent, (id) => kindOf(outline.byId.get(id)!) === 'Document') : null;
  const level = rings ? null : levels(outline);
  const depthOf = (id: string) => (rings ? rings.position.get(id)?.depth ?? 0 : level!.get(id) ?? 0);
  const deepest = Math.max(0, ...fold.shown.map(depthOf));
  const placed = layout === 'tree' ? treeLayout(fold.shown, outline.parent) : null;
  return { shown: fold.shown, held, canFold, depthOf, deepest, rings, placed };
}

/** The rings behind the nodes: a band per level of the outline, alternately shaded, its edge
 * drawn and its level named. Lines and names keep their size on screen however far it is zoomed. */
export function drawRings(ctx: CanvasRenderingContext2D, radius: number[], scale: number,
  look: { bands: string[]; line: string; text: string }, t: T) {
  const outer = (d: number) => (d + 1 < radius.length ? (radius[d] + radius[d + 1]) / 2 : radius[d] + RING_STEP / 2);
  ctx.save();
  for (let d = radius.length - 1; d >= 0; d--) {
    ctx.beginPath(); ctx.arc(0, 0, outer(d), 0, 2 * Math.PI);
    ctx.fillStyle = look.bands[d % 2];
    ctx.fill();
  }
  const px = 1 / scale;
  ctx.setLineDash([6 * px, 6 * px]); ctx.lineWidth = px; ctx.strokeStyle = look.line;
  ctx.font = `600 ${12 * px}px ${FONT}`; ctx.fillStyle = look.text; ctx.textAlign = 'center';
  for (let d = 1; d < radius.length; d++) {
    ctx.beginPath(); ctx.arc(0, 0, radius[d], 0, 2 * Math.PI); ctx.stroke();
    ctx.fillText(t('level', d), 0, -radius[d] - 8 * px);
  }
  ctx.restore();
}

/** Which drawn node stands for each unit a passage covers -- itself, or what it is folded into --
 * and the outline edges joining them. */
export function chunkSpans(outline: Outline, shown: string[]) {
  const drawn = new Set(shown);
  const standIn = (id: string) => {
    for (let x: string | undefined = id, step = 0; x !== undefined && step < 64; step++) {
      if (drawn.has(x)) return x;
      const node = outline.byId.get(x);
      x = (node && hostOf(outline, node)) || outline.parent(x);
    }
    return undefined;
  };
  const drawnParent = (id: string) => { const p = outline.parent(id); return p && drawn.has(p) ? p : undefined; };
  return (outline.graph.chunks ?? []).map((chunk, index) => {
    const ids = [...new Set(chunk.members.map(standIn).filter((x): x is string => !!x))];
    return { index, importance: chunk.importance ?? 0.5, ids, edges: treeSpan(ids, drawnParent) };
  }).filter((span) => span.ids.length);
}

/** The nodes and lines a drawing shows, as vis takes them. Every property a later drawing may
 * change is always given -- a label emptied rather than left out -- since vis keeps what an
 * update does not mention. */
export function visElements(outline: Outline, graph: DocumentGraph, drawn: ReturnType<typeof drawing>,
  { t, layout, shownTypes, showExternal }: { t: T; layout: Layout; shownTypes: Record<string, boolean>; showExternal: boolean }) {
  const { shown, held, canFold, depthOf, deepest, rings, placed } = drawn;
  const visible = new Set(shown);
  const fg = token('--gray-12');
  const ringFont = { size: 10, color: fg, face: FONT, strokeWidth: 3, strokeColor: token('--gray-1') };
  const nodes: Node[] = shown.map((id) => {
    const n = outline.byId.get(id)!;
    const kind = kindOf(n);
    const depth = depthOf(id);
    const caption = labelOf(n, t);
    const node = neoNode(id, kind, caption, {
      level: depth, fill: shade(colorOf(kind), depth, deepest),
      title: tip(`${t('nodeLevel', kind, depth)}\n${clip(shownName(n.properties.text ?? n.properties.label), 160)}`) as unknown as string,
    });
    const at = rings?.position.get(id) ?? placed?.get(id);
    if (rings && at) Object.assign(node, { x: at.x, y: at.y, shape: 'dot', size: sizeAt(depth), font: ringFont, label: depth <= 4 ? clip(caption, 16) : '' });
    else if (at) Object.assign(node, { x: at.x, y: at.y });
    const hidden = held(id);
    if (hidden) {
      // Folded is a triangle, open is a circle: what is still hidden is plain at a glance.
      Object.assign(node, { shape: 'triangle', size: NODE_SIZE, label: `${clip(caption, 12)} +${hidden}`, borderWidth: 2,
        title: tip(`${t('nodeLevel', kind, depth)}\n\n${t('foldedTip', hidden)}`), font: ringFont });
      node.color = { ...(node.color as object), border: fg };
    } else if (canFold(id)) node.title = tip(`${t('nodeLevel', kind, depth)}\n\n${t('foldTip')}`) as unknown as string;
    return node;
  });

  // Shown, a cited document hangs under the units that cite it, not off the root.
  const linkedTo = new Set(graph.relationships.filter((r) => r.type === 'SAME_AS').map((r) => r.start));
  const citedFromView = new Set(graph.relationships.filter((r) => r.type === 'REFERENCES' && visible.has(r.start)).map((r) => r.end));
  const edges: Edge[] = graph.relationships.filter((r) => {
    if (!visible.has(r.start) || !visible.has(r.end)) return false;
    if (r.type === 'REFERENCES') return showExternal || linkedTo.has(r.end);
    if (r.type === 'PARENT_OF' && citedFromView.has(r.end)) return false;
    return shownTypes[r.type] ?? edgeStyle(r.type).on;
  }).map((r) => {
    const e = neoEdge({ id: r.id, from: r.start, to: r.end, title: r.type }, r.type, edgeStyle(r.type));
    // On the rings the outline's own lines say nothing the rings do not: faint, uncaptioned, straight.
    if (rings) Object.assign(e, { label: '', smooth: false, width: 2.2, arrows: { to: { enabled: true, scaleFactor: 0.4 } } });
    if (layout === 'tree') e.smooth = { enabled: true, type: 'cubicBezier', forceDirection: 'vertical', roundness: 0.45 };
    return e;
  });
  return { nodes, edges };
}

/** `items` as the whole of `set`: what is no longer among them removed, the rest updated in place. */
export function replaceAll<T extends { id?: string | number }>(set: DataSet<T>, items: T[]) {
  const keep = new Set(items.map((item) => item.id));
  set.remove(set.getIds().filter((id) => !keep.has(id)));
  set.update(items as never);
}
