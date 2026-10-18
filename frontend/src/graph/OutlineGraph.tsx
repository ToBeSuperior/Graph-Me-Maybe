// A document's outline drawn: on rings (a band per level), as a tree in reading order, or laid
// out by force the way Neo4j's browser does; or as an indented list. Each passage the document
// is cut into is a soft shape behind the nodes it covers, darker the more central it is. A node
// holding much starts folded into a triangle marked +N; double-click folds or opens any node.
import { useEffect, useMemo, useRef, useState } from 'react';
import { DataSet, Network, type Edge, type Node, type Options } from 'vis-network/standalone';
import type { DocumentGraph } from '../api/client';
import { Maximize, Minus, Plus } from 'lucide-react';
import { Button } from '../components/ui';
import { cn } from '../lib/format';
import { useT } from '../lib/i18n';
import { token, useThemeTick } from '../lib/theme';
import { band, chunkSpans, CHUNK_PAD, drawing, drawRings, replaceAll, visElements, type Layout } from './draw';
import { EDGE, INTERACTION, PHYSICS, colorOf, edgeStyle, paint, shade, wheelAfterClick } from './look';
import { OutlineList } from './OutlineList';
import { kindOf, outlineOf } from './outline';

const READABLE = 0.6;
/** When the page's nodes only fit below READABLE, its top node is put this far above the middle
 * of the view, so the first levels under it are what is on screen. */
const TOP_ABOVE_CENTRE = 180;
const DOUBLE_CLICK_MS = 250;

export function OutlineGraph({ graph, pages, onPick, frame, picked }: {
  graph: DocumentGraph;
  /** The page each node is drawn on, when the pages are known. */
  pages: Record<string, number>;
  onPick: (id: string | null) => void;
  /** A page whose nodes are framed. */
  frame: number | null;
  picked: string | null;
}) {
  const t = useT();
  const tick = useThemeTick();
  const box = useRef<HTMLDivElement>(null);
  const net = useRef<Network | null>(null);
  const [layout, setLayout] = useState<Layout>(() => { try { return (localStorage.getItem('graphLayoutV2') as Layout) || 'radial'; } catch { return 'radial'; } });
  const [view, setView] = useState<'graph' | 'list'>('graph');
  const [folding, setFolding] = useState(new Map<string, boolean>());
  const [showChunks, setShowChunks] = useState(true);
  const [showExternal, setShowExternal] = useState(false);
  const [shownTypes, setShownTypes] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(Object.entries(EDGE).map(([k, v]) => [k, v.on])));
  const outline = useMemo(() => outlineOf(graph), [graph]);
  const pick = useRef(onPick);
  pick.current = onPick;

  const { kinds, rels } = useMemo(() => {
    const kinds: Record<string, number> = {};
    const rels: Record<string, number> = {};
    graph.nodes.forEach((n) => { kinds[kindOf(n)] = (kinds[kindOf(n)] ?? 0) + 1; });
    graph.relationships.forEach((r) => { rels[r.type] = (rels[r.type] ?? 0) + 1; });
    return { kinds, rels };
  }, [graph]);

  const drawn = useMemo(() => drawing(outline, folding, showExternal, layout), [outline, folding, showExternal, layout]);
  // Read by the effects that must not run again each time the drawing does.
  const drawnNow = useRef(drawn);
  drawnNow.current = drawn;
  const pickedNow = useRef(picked);
  pickedNow.current = picked;
  /** Where the view stood when the drawing was last taken down, to stand there again. */
  const lastView = useRef<{ layout: Layout; position: { x: number; y: number }; scale: number } | null>(null);

  /** The network's data, changed in place: a fold or a toggle updates what is drawn rather
   * than drawing everything again -- in the Neo4j layout the forces carry on from where they
   * were instead of settling the whole graph anew. `fresh` until the first drawing lands. */
  const live = useRef<{ nodes: DataSet<Node>; edges: DataSet<Edge>; fresh: boolean } | null>(null);
  /** What the network's handlers and painting read: the drawing as it is now. */
  const now = useRef({ drawn, spans: [] as ReturnType<typeof chunkSpans> });

  // The network itself: made again only when the layout, the view or the colours change.
  useEffect(() => {
    if (!box.current || view !== 'graph') return;
    const nodes = new DataSet<Node>();
    const edges = new DataSet<Edge>();
    const options: Options = layout === 'force'
      ? { layout: { improvedLayout: false }, physics: PHYSICS, interaction: INTERACTION }
      : { layout: { improvedLayout: false }, physics: false, interaction: INTERACTION };
    const network = new Network(box.current, { nodes, edges }, options);
    net.current = network;
    live.current = { nodes, edges, fresh: true };

    const look = { bands: [token('--gray-1'), token('--gray-3')], line: token('--gray-6'), text: token('--gray-11') };
    network.on('beforeDrawing', (ctx: CanvasRenderingContext2D) => {
      const { drawn: shownNow, spans } = now.current;
      if (shownNow.rings) drawRings(ctx, shownNow.rings.radius, network.getScale(), look, t);
      for (const span of spans) {
        const at = network.getPositions([...span.ids, ...span.edges.flat()]);
        const points = span.ids.map((id) => at[id]).filter(Boolean);
        if (!points.length) continue;
        ctx.beginPath();
        points.forEach((p) => { ctx.moveTo(p.x + CHUNK_PAD, p.y); ctx.arc(p.x, p.y, CHUNK_PAD, 0, 2 * Math.PI); });
        span.edges.forEach(([a, b]) => { if (at[a] && at[b]) band(ctx, at[a], at[b], CHUNK_PAD * 0.55); });
        ctx.fillStyle = `hsla(${(span.index * 137.5) % 360}, 70%, 55%, ${0.05 + 0.15 * span.importance})`;
        ctx.fill('nonzero');
      }
    });

    // A click opens the node's detail -- after a moment, so the second click of a double-click
    // does not land on wherever the detail moved the view.
    let pending: ReturnType<typeof setTimeout>;
    network.on('click', (p) => { clearTimeout(pending); pending = setTimeout(() => pick.current(p.nodes[0] ?? null), DOUBLE_CLICK_MS); });
    network.on('doubleClick', (p) => {
      clearTimeout(pending);
      const id = p.nodes[0] as string | undefined;
      const { held, canFold } = now.current.drawn;
      if (!id || !(held(id) || canFold(id))) return;
      setFolding((m) => new Map(m).set(id, !held(id)));
    });
    const unbind = wheelAfterClick(box.current);
    // Taken down when the layout, the view or the colours change: where it stood is kept.
    return () => {
      clearTimeout(pending);
      unbind();
      lastView.current = { layout, position: network.getViewPosition(), scale: network.getScale() };
      network.destroy();
      if (net.current === network) net.current = null;
      live.current = null;
    };
  }, [view, layout, tick, t]);

  // What is drawn, changed in place: nodes and lines no longer shown taken away, the rest
  // updated or added.
  useEffect(() => {
    const target = live.current;
    const network = net.current;
    if (!target || !network || view !== 'graph') return;
    const { nodes, edges } = visElements(outline, graph, drawn, { t, layout, shownTypes, showExternal });
    // By force, a node already drawn stays where the forces put it.
    if (layout === 'force') nodes.forEach((node) => { if (target.nodes.get(node.id as string)) { delete node.x; delete node.y; } });
    replaceAll(target.nodes, nodes);
    replaceAll(target.edges, edges);
    now.current = { drawn, spans: showChunks ? chunkSpans(outline, drawn.shown) : [] };
    if (target.fresh) {
      target.fresh = false;
      const keep = lastView.current?.layout === layout ? lastView.current : null;
      if (keep) network.moveTo({ position: keep.position, scale: keep.scale });
      else if (layout !== 'force') network.fit();
      else network.once('stabilizationIterationsDone', () => network.fit({ animation: { duration: 400, easingFunction: 'easeInOutQuad' } }));
    }
    // Drawn again (a fold, a toggle), the picked node stays picked.
    if (pickedNow.current && drawn.shown.includes(pickedNow.current)) network.selectNodes([pickedNow.current]);
    network.redraw();
  }, [drawn, view, tick, shownTypes, showChunks, showExternal, outline, graph, layout, t]);

  // The picked node selected where it stands -- the view is not moved under the pointer, or the
  // second click of a double-click lands somewhere else.
  useEffect(() => {
    const network = net.current;
    if (!network || !picked || view !== 'graph' || !drawn.shown.includes(picked)) return;
    network.selectNodes([picked]);
  }, [picked, drawn, view]);

  // The frame changing size (the page settling, the window) fits the drawing again, until the
  // reader has moved it -- a size it already had is no change.
  useEffect(() => {
    const el = box.current;
    if (!el || view !== 'graph') return;
    let moved = false;
    let size = `${el.clientWidth}x${el.clientHeight}`;
    const mark = () => { moved = true; };
    el.addEventListener('pointerdown', mark);
    el.addEventListener('wheel', mark);
    const watch = new ResizeObserver(() => {
      const now = `${el.clientWidth}x${el.clientHeight}`;
      if (now !== size && !moved) net.current?.fit();
      size = now;
    });
    watch.observe(el);
    return () => { watch.disconnect(); el.removeEventListener('pointerdown', mark); el.removeEventListener('wheel', mark); };
  }, [view]);

  // A page's own nodes framed when the page changes -- not each time something is folded, and
  // not for the page a document opens on: that is the whole drawing, fitted.
  const framed = useRef<number | null>(null);
  useEffect(() => {
    const network = net.current;
    // The first page known is the one it opened on, however late the pages arrive.
    const opening = framed.current === null;
    framed.current = frame;
    if (opening || !network || frame === null || view !== 'graph') return;
    const ids = drawnNow.current.shown.filter((id) => pages[id] === frame);
    if (!ids.length) return;
    network.fit({ nodes: ids });
    if (network.getScale() >= READABLE) return;
    const top = ids.find((id) => !ids.includes(outline.parent(id) ?? '')) ?? ids[0];
    const at = network.getPositions([top])[top];
    network.moveTo({ position: { x: at.x, y: at.y + TOP_ABOVE_CENTRE }, scale: READABLE });
  }, [frame, pages, view, outline]);

  const zoom = (by: number | 'fit') => {
    const network = net.current;
    if (!network) return;
    if (by === 'fit') network.fit({ animation: { duration: 300, easingFunction: 'easeInOutQuad' } });
    else network.moveTo({ position: network.getViewPosition(), scale: network.getScale() * by, animation: { duration: 150, easingFunction: 'easeInOutQuad' } });
  };

  const seg = (on: boolean) => cn('h-7 rounded-md px-2.5 text-xs font-medium', on ? 'bg-panel text-fg shadow-sm' : 'text-muted hover:text-fg');
  return (
    <div className="flex h-full flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex rounded-lg bg-surface p-0.5" role="group" aria-label={t('layout')}>
          {(['radial', 'tree', 'force'] as const).map((l) => (
            <button key={l} className={seg(layout === l)} aria-pressed={layout === l}
              onClick={() => { setLayout(l); try { localStorage.setItem('graphLayoutV2', l); } catch { /* storage off */ } }}>
              {t(l === 'radial' ? 'layoutRadial' : l === 'tree' ? 'layoutTree' : 'layoutForce')}
            </button>
          ))}
        </div>
        <div className="flex rounded-lg bg-surface p-0.5" role="group">
          <button className={seg(view === 'graph')} aria-pressed={view === 'graph'} onClick={() => setView('graph')}>{t('graph')}</button>
          <button className={seg(view === 'list')} aria-pressed={view === 'list'} onClick={() => setView('list')}>{t('list')}</button>
        </div>
        <span className="ml-auto font-mono text-2xs text-faint">{t('nodes', graph.nodes.length)} · {t('relationships', graph.relationships.length)}</span>
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted">
        {Object.keys(kinds).map((k) => (
          <span key={k} className="inline-flex items-center gap-1">
            <i className="inline-block h-2.5 w-4 rounded-sm" style={{ background: `linear-gradient(90deg, ${colorOf(k)}, ${shade(colorOf(k), 1, 1)})` }} />{k} {kinds[k]}
          </span>
        ))}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted">
        {Object.keys(rels).sort().filter((type) => type !== 'REFERENCES').map((type) => {
          const s = edgeStyle(type);
          return (
            <label key={type} className="inline-flex cursor-pointer items-center gap-1.5">
              <input type="checkbox" checked={shownTypes[type] ?? s.on} onChange={(e) => setShownTypes({ ...shownTypes, [type]: e.target.checked })} />
              <i className="inline-block w-4 border-t-2" style={{ borderColor: paint(s.color), borderTopStyle: s.dashes ? 'dashed' : 'solid' }} />
              {EDGE[type] ? t(s.name as 'edgeParent') : s.name} {rels[type]}
            </label>
          );
        })}
        {!!graph.chunks?.length && (
          <label className="inline-flex cursor-pointer items-center gap-1.5">
            <input type="checkbox" checked={showChunks} onChange={(e) => setShowChunks(e.target.checked)} />
            <i className="inline-block h-2.5 w-4 rounded" style={{ background: 'hsla(30,70%,55%,.3)' }} />{t('chunkSpans', graph.chunks.length)}
          </label>
        )}
        {!!kinds.ExternalDocument && (
          <label className="inline-flex cursor-pointer items-center gap-1.5">
            <input type="checkbox" checked={showExternal} onChange={(e) => setShowExternal(e.target.checked)} />
            {t('showReferences', kinds.ExternalDocument, rels.REFERENCES ?? 0)}
          </label>
        )}
      </div>
      <div className="relative min-h-[360px] flex-1 overflow-hidden rounded-xl border border-line bg-subtle">
        {view === 'graph' ? (
          <>
            <div ref={box} className="absolute inset-0" />
            <div className="absolute right-2 bottom-2 flex gap-1 rounded-lg border border-line bg-panel/90 p-0.5 shadow-sm backdrop-blur">
              <Button variant="ghost" size="icon-sm" aria-label={t('zoomIn')} onClick={() => zoom(1.3)}><Plus className="size-3.5" /></Button>
              <Button variant="ghost" size="icon-sm" aria-label={t('zoomOut')} onClick={() => zoom(1 / 1.3)}><Minus className="size-3.5" /></Button>
              <Button variant="ghost" size="icon-sm" aria-label={t('zoomFit')} onClick={() => zoom('fit')}><Maximize className="size-3.5" /></Button>
            </div>
            <p className="pointer-events-none absolute bottom-2 left-3 max-w-[60%] rounded bg-panel/80 px-1.5 text-2xs text-faint">{t(layout === 'radial' ? 'layoutNoteRadial' : layout === 'tree' ? 'layoutNoteTree' : 'layoutNoteForce')} · {t('foldKey')}</p>
          </>
        ) : (
          <OutlineList outline={outline} showExternal={showExternal} picked={picked} onPick={onPick} />
        )}
      </div>
    </div>
  );
}
