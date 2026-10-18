// A document's own graph read as an outline: each node's one parent, the visual a node is part
// of, how deep it sits, and what it is called on the drawing.
import type { DocumentGraph, GraphNode } from '../api/client';
import type { T } from '../lib/i18n';
import { clip, shownName } from '../lib/format';

export interface Outline {
  graph: DocumentGraph;
  byId: Map<string, GraphNode>;
  /** The outline parent: one PARENT_OF per node; a caption hangs off what it describes. */
  parent: (id: string) => string | undefined;
  /** What the file holds the node in, where the page was read and moved it. */
  owner: Map<string, string>;
}

export const kindOf = (n: GraphNode) => n.labels[1] ?? n.labels[0];
const VISUALS = ['Table', 'PivotTable', 'Image', 'Chart'];

export function outlineOf(graph: DocumentGraph): Outline {
  const parents = new Map<string, string>();
  const owner = new Map<string, string>();
  for (const r of graph.relationships) {
    if (r.type === 'PARENT_OF') {
      parents.set(r.end, r.start);
      owner.set(r.end, (r.properties.meta_file_parent_id as string | undefined) ?? r.start);
    } else if (r.type === 'CAPTION_OF') {
      // A caption has no place in the outline; it hangs off what it describes.
      parents.set(r.start, r.end);
    }
  }
  return { graph, byId: new Map(graph.nodes.map((n) => [n.id, n])), parent: (id) => parents.get(id), owner };
}

/** The visual a node is part of -- a figure's words and drawings, a table's cells -- folded into
 * it until it is opened, since the crop already shows them in place. A caption is what the visual
 * is for, and stays beside it. */
export function hostOf(o: Outline, n: GraphNode): string | undefined {
  if (kindOf(n) === 'VlmEnrichedCaption') return undefined;
  const part = n.properties.meta_part_of_figure as string | undefined;
  if (part) return part;
  const host = o.byId.get(o.owner.get(n.id) ?? '');
  return host && VISUALS.includes(kindOf(host)) ? host.id : undefined;
}

export function levels(o: Outline) {
  const memo = new Map<string, number>();
  const depth = (id: string, seen = new Set<string>()): number => {
    if (memo.has(id)) return memo.get(id)!;
    const p = o.parent(id);
    const d = p === undefined || seen.has(id) || !o.byId.has(p) ? 0 : depth(p, seen.add(id)) + 1;
    memo.set(id, d);
    return d;
  };
  o.graph.nodes.forEach((n) => depth(n.id));
  return memo;
}

/** What a node is called on the drawing. A sheet's table by the cells it covers; a drawing by
 * what it is, numbered -- its own label is what the authoring tool called the shape. */
export function labelOf(n: GraphNode, t: T) {
  const p = n.properties as Record<string, string | undefined>;
  const kind = kindOf(n);
  const number = (p.label ?? '').match(/\d+\s*$/)?.[0] ?? '';
  if (kind === 'Table') {
    if (p.cell_range) return `${t('table')} ${p.cell_range}`;
    if (/^표\s*\d+$/.test(p.label ?? '')) return `${t('table')} ${p.label!.match(/\d+/)![0]}`;
    return clip(`${t('table')} ${p.label ?? ''}`.trim(), 18);
  }
  if (kind === 'Image') return `${t('figure')} ${number}`.trim();
  if (kind === 'Chart') return `${t('chart')} ${number}`.trim();
  if (kind === 'TextChunk') return clip(p.text || p.label, 18);
  return clip(kind === 'ExternalDocument' || kind === 'Document' ? shownName(p.label ?? '') : p.label, 22);
}

/** The nodes hidden before any folding by hand: a document this one names (a stub off the root,
 * one per name) unless shown or joined to a document here, and what a visual holds unless it was
 * opened. */
/** `folding` undefined: every visual open. */
export function hiddenBy(o: Outline, folding: Map<string, boolean> | undefined, showExternal: boolean) {
  const hidden = new Set<string>();
  const linked = new Set(o.graph.relationships.filter((r) => r.type === 'SAME_AS').map((r) => r.start));
  if (!showExternal) {
    for (const n of o.graph.nodes) if (kindOf(n) === 'ExternalDocument' && !linked.has(n.id)) hidden.add(n.id);
  }
  for (const n of o.graph.nodes) {
    const host = hostOf(o, n);
    if (host && folding && folding.get(host) !== false) hidden.add(n.id);
  }
  return hidden;
}
