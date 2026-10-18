// How the graphs are drawn, the way Neo4j's own browser draws one: a node filled in its label's
// colour, one thin line per relationship captioned with its type, and -- in the force layout --
// physics that keeps running, so a dragged node pulls its neighbours along. Colours are read
// from the theme's tokens when a graph is drawn (`token`), so a theme change draws it again.
import type { Edge, Node, Options } from 'vis-network/standalone';
import { clip } from '../lib/format';
import { token } from '../lib/theme';

export const FONT = '"Pretendard Variable", Pretendard, -apple-system, sans-serif';

/** Neo4j label -> colour. A page-like container (sheet, slide) shares one tone. */
const KIND: Record<string, string> = {
  Document: '#F79767', Workbook: '#FFC454', Worksheet: '#FFC454', Slide: '#FFC454',
  TextChunk: '#57C7E3', Table: '#8DCC93', PivotTable: '#8DCC93', Image: '#F16667', Chart: '#C990C0',
  VlmEnrichedCaption: '#D9C8AE', ExternalDocument: '#ECB5C9',
};
export const colorOf = (kind: string) => KIND[kind] ?? '#A5ABB6';
const INK = '#1F2125';

/** How deep a node sits is how pale it is: its kind's colour at the root, mixed toward white one
 * step per level, so the outline reads from the strong centre out to the pale edge. */
const PALEST = 0.45;
export function shade(hex: string, depth: number, deepest: number) {
  const k = deepest > 0 ? (PALEST * Math.min(depth, deepest)) / deepest : 0;
  const rgb = hex.replace('#', '').match(/../g)!.map((h) => parseInt(h, 16));
  return `#${rgb.map((c) => Math.round(c + (255 - c) * k).toString(16).padStart(2, '0')).join('')}`;
}

interface EdgeStyle { color: string; width: number; dashes: boolean | number[]; on: boolean; name: string }

/** The outline's lines and the ties drawn over it. `color` is a token or a colour. */
export const EDGE: Record<string, EdgeStyle> = {
  PARENT_OF: { color: '--gray-8', width: 2.6, dashes: false, on: true, name: 'edgeParent' },
  NEXT: { color: '--gray-8', width: 2, dashes: [5, 3], on: false, name: 'edgeNext' },
  CAPTION_OF: { color: '--gray-8', width: 2, dashes: false, on: true, name: 'edgeCaption' },
  REFERENCES: { color: '--gray-8', width: 2, dashes: [2, 3], on: false, name: 'edgeReferences' },
  SAME_AS: { color: '--rel-cites', width: 2.5, dashes: false, on: true, name: 'edgeSameAs' },
  SIMILAR_TO: { color: '--rel-similar', width: 1.5, dashes: [6, 4], on: true, name: 'edgeSimilar' },
};
export const edgeStyle = (type: string): EdgeStyle =>
  EDGE[type] ?? { color: '--gray-8', width: 1.2, dashes: [2, 3], on: false, name: type };
export const paint = (color: string) => (color.startsWith('--') ? token(color) : color);

export function neoNode(id: string, kind: string, caption: string, extra: Partial<Node> & { fill?: string } = {}): Node {
  const { fill = colorOf(kind), ...rest } = extra;
  const fg = token('--gray-12');
  return {
    id, label: clip(caption, 18), shape: 'circle', widthConstraint: { minimum: 34, maximum: 34 },
    color: { background: fill, border: fill, highlight: { background: fill, border: fg }, hover: { background: fill, border: fg } },
    borderWidth: 2, borderWidthSelected: 4,
    font: { size: 9, color: INK, face: FONT },
    ...rest,
  };
}

export function neoEdge(edge: Edge, caption: string | undefined, style: EdgeStyle): Edge {
  const fg = token('--gray-12');
  return {
    ...edge, label: caption, width: style.width, dashes: style.dashes,
    color: { color: paint(style.color), highlight: fg, hover: fg, opacity: 0.9 },
    font: { size: 9, color: token('--gray-11'), face: FONT, strokeWidth: 3, strokeColor: token('--gray-1'), align: 'middle' },
    arrows: { to: { enabled: true, scaleFactor: 0.45 } }, smooth: { enabled: true, type: 'dynamic', roundness: 0.5 },
  };
}

/** d3-force, as Neo4j uses it, is closest to vis' force-atlas solver: nodes repel, lines pull
 * like springs, and enough damping that a drag settles instead of springing back and forth. */
export const PHYSICS: Options['physics'] = {
  enabled: true, solver: 'forceAtlas2Based',
  forceAtlas2Based: { gravitationalConstant: -70, centralGravity: 0.012, springLength: 110, springConstant: 0.07, damping: 0.55, avoidOverlap: 0.6 },
  stabilization: { enabled: true, iterations: 400, updateInterval: 50, fit: true }, minVelocity: 0.6, timestep: 0.4,
};
export const INTERACTION: Options['interaction'] = {
  hover: true, tooltipDelay: 200, dragNodes: true, zoomSpeed: 0.6, hideEdgesOnDrag: false, multiselect: false,
};

/** A wheel over a graph scrolls the page until the graph is clicked; then it zooms, until the
 * pointer leaves. Scrolling past a graph otherwise zoomed it instead. */
export function wheelAfterClick(box: HTMLElement) {
  let armed = false;
  const down = () => { armed = true; };
  const leave = () => { armed = false; };
  // Stopped on its way down, the wheel never reaches the network and scrolls the page.
  const wheel = (e: WheelEvent) => { if (!armed) e.stopPropagation(); };
  box.addEventListener('pointerdown', down);
  box.addEventListener('pointerleave', leave);
  box.addEventListener('wheel', wheel, { capture: true });
  return () => {
    box.removeEventListener('pointerdown', down);
    box.removeEventListener('pointerleave', leave);
    box.removeEventListener('wheel', wheel, { capture: true });
  };
}

/** A tooltip vis shows as text, not markup: an element holding the text. */
export function tip(text: string) {
  const el = document.createElement('div');
  el.style.whiteSpace = 'pre-wrap';
  el.style.maxWidth = '320px';
  el.textContent = text;
  return el;
}
