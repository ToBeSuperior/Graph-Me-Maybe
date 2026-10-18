// The documents of the graph and how they are tied: an arrow from a document to one it cites,
// thicker the more it names it, and a dashed line between documents that say alike things.
// Picking one ringed its neighbours; opening it shows its outline. A space of thousands draws
// its best-linked documents only (the server says how many there are in all).
import { AlertTriangle, Network as NetworkIcon } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { DataSet, Network, type Edge, type Node } from 'vis-network/standalone';
import { Button, Empty, Skeleton } from '../../components/ui';
import { EDGE, INTERACTION, PHYSICS, neoEdge, neoNode, paint, tip, wheelAfterClick } from '../../graph/look';
import { formatOf, shownName, stem } from '../../lib/format';

import { useT } from '../../lib/i18n';
import { useCorpusMap } from '../../lib/queries';
import { token, useThemeTick } from '../../lib/theme';
import { FolderDrop } from '../upload/FolderDrop';
import { PageHeader } from './ManageLayout';

/** A document's colour on the map is its format's, as its icon is everywhere else. */
const FORMAT_FILL: Record<string, string> = { docx: '#8DB4F0', xlsx: '#8DCC93', pptx: '#F4A58A', pdf: '#F08A8A' };
const OTHER_FILL = '#F79767';

export function MapPage() {
  const t = useT();
  const navigate = useNavigate();
  const tick = useThemeTick();
  const { data: map, isPending, isError, error } = useCorpusMap();
  const box = useRef<HTMLDivElement>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const net = useRef<Network | null>(null);
  /** The map's nodes, held so a pick repaints them in place. */
  const shownNodes = useRef<DataSet<Node> | null>(null);
  const fills = useMemo(() => new Map((map?.documents ?? []).map((d) => [d.id, FORMAT_FILL[formatOf(d.label)] ?? OTHER_FILL])), [map]);

  useEffect(() => {
    if (!map || !box.current || !map.documents.length) return;
    const nodes: Node[] = map.documents.map((d) => neoNode(d.id, 'Document', stem(d.label), {
      fill: fills.get(d.id) ?? OTHER_FILL,
      title: tip(`${shownName(d.label)}${d.title ? `\n${d.title}` : ''}\n${t('mentions', d.mentions ?? 0)}`) as unknown as string,
      widthConstraint: { minimum: 120, maximum: 120 }, font: { size: 14, color: '#1F2125', face: 'Pretendard Variable' },
    }));
    const edges: Edge[] = [
      ...map.links.map((l) => neoEdge({ from: l.from, to: l.to, title: tip(t('matchedBy', l.matched_by.join(', '))) as unknown as string },
        t('mapCites', l.count), { ...EDGE.SAME_AS, width: 1.5 + Math.min(l.count, 4) * 0.5 })),
      ...map.similar.map((l) => ({ ...neoEdge({ from: l.from, to: l.to, title: tip(t('similarScore', l.score.toFixed(2))) as unknown as string },
        `≈ ${l.score.toFixed(2)}`, EDGE.SIMILAR_TO), arrows: { to: { enabled: false } } })),
    ];
    const dataset = new DataSet<Node>(nodes);
    shownNodes.current = dataset;
    const network = new Network(box.current, { nodes: dataset, edges }, {
      // Documents nothing ties together drift to the edge, as they do in Neo4j's browser.
      physics: { ...PHYSICS, forceAtlas2Based: { ...(PHYSICS as { forceAtlas2Based: object }).forceAtlas2Based, springLength: 150, gravitationalConstant: -90, centralGravity: 0.02 } },
      // The same documents settle in the same places each time the map is drawn.
      // vis's own first placement is slow past a hundred nodes; the forces place them as well.
      interaction: INTERACTION, layout: { improvedLayout: nodes.length < 100, randomSeed: 7 },
    });
    network.once('stabilized', () => network.fit({ animation: { duration: 400, easingFunction: 'easeInOutQuad' } }));
    network.on('click', (p) => setPicked(p.nodes[0] ?? null));
    network.on('doubleClick', (p) => { if (p.nodes[0]) navigate(`/manage/docs/${encodeURIComponent(p.nodes[0])}`); });
    net.current = network;
    const unbind = wheelAfterClick(box.current);
    return () => { unbind(); network.destroy(); net.current = null; shownNodes.current = null; };
  }, [map, fills, tick, t, navigate]);

  // The picked document filled, the documents tied to it either way ringed.
  useEffect(() => {
    const data = shownNodes.current;
    if (!data || !map) return;
    const tied = new Set([...map.links, ...map.similar].flatMap((l) => (l.from === picked ? [l.to] : l.to === picked ? [l.from] : [])));
    const ring = token('--accent-9');
    const fill = (id: string) => fills.get(id) ?? OTHER_FILL;
    data.update(data.getIds().map((id) => ({
      id: id as string,
      borderWidth: id === picked || tied.has(id as string) ? 4 : 2,
      color: { background: id === picked ? paint('--accent-6') : fill(id as string), border: id === picked ? ring : tied.has(id as string) ? paint('--rel-similar') : fill(id as string) },
    })));
    // With the drawing's own deps too: drawn again (theme, language), it is painted again.
  }, [picked, map, tick, t, fills]);

  const pickedDoc = map?.documents.find((d) => d.id === picked);
  const linkCount = map ? map.links.length + map.similar.length : 0;

  return (
    <div className="flex h-full flex-col">
      <PageHeader title={t('mapTitle')}
        description={map ? (map.total > map.documents.length ? t('mapCapped', map.documents.length, map.total) : t('mapSummary', map.total, linkCount)) : undefined} />
      <div className="flex min-h-0 flex-1 flex-col gap-3 p-6">
        {isPending ? <Skeleton className="flex-1" />
          : isError ? <Empty icon={<AlertTriangle className="size-5" />} title={t('dbUnreachable')}>{(error as Error).message}</Empty>
          : !map?.documents.length ? <FolderDrop large />
          : (
            <>
              <div className="relative min-h-[420px] flex-1 overflow-hidden rounded-xl border border-line bg-subtle">
                <div ref={box} className="absolute inset-0" />
                {pickedDoc && (
                  <div className="absolute top-3 left-3 flex max-w-[calc(100%-24px)] items-center gap-2 rounded-lg border border-line bg-panel/95 px-3 py-2 shadow-pop backdrop-blur">
                    <NetworkIcon className="size-4 shrink-0 text-accent" />
                    <span className="truncate text-sm font-medium">{shownName(pickedDoc.label)}</span>
                    <Button size="sm" variant="primary" onClick={() => navigate(`/manage/docs/${encodeURIComponent(pickedDoc.id)}`)}>{t('openOutline')}</Button>
                  </div>
                )}
              </div>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
                <span className="inline-flex items-center gap-1.5"><i className="inline-block h-0.5 w-5 bg-cites" />{t('legendCites')}</span>
                <span className="inline-flex items-center gap-1.5"><i className="inline-block w-5 border-t-2 border-dashed border-similar" />{t('legendSimilar')}</span>
                {Object.entries(FORMAT_FILL).map(([f, c]) => (
                  <span key={f} className="inline-flex items-center gap-1 font-mono uppercase"><i className="inline-block size-2.5 rounded-full" style={{ background: c }} />{f}</span>
                ))}
                <span className="text-faint">{t('mapHint')}</span>
              </div>
            </>
          )}
      </div>
    </div>
  );
}
