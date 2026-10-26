// One document: its outline on the left, its rendered pages on the right, and -- a node picked --
// that node's detail in place of the page. A document of the graph is read from the graph; one a
// folder brought that is not in it yet (`?demo=1`) is followed while the demo reads it, its
// outline growing as each page's answer lands.
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, ArrowLeft, ChevronLeft, FileQuestion } from 'lucide-react';
import { useMemo, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { api, call, isBuilding, nodeCrop, pageImage, type DocumentGraph, type Pages } from '../../api/client';
import { FileIcon } from '../../components/docs';
import { PageViewer } from '../../components/PageViewer';
import { Badge, Button, Empty, Skeleton, Spinner } from '../../components/ui';
import { colorOf } from '../../graph/look';
import { OutlineGraph } from '../../graph/OutlineGraph';
import { kindOf, outlineOf } from '../../graph/outline';
import { clip, formatOf, shownName, stem } from '../../lib/format';
import { useT } from '../../lib/i18n';
import { reason } from '../../lib/reasons';
import { keys, useBuild } from '../../lib/queries';
import { pageName, pageNameAt, usePages } from '../../lib/pages';
import { PagesPending, PageTabs } from '../../components/PageTabs';
import { DeleteDocument } from './DeleteDocument';

const WORKING = ['rendering', 'grouping', 'reading', 'captioning', 'referencing'];

export function DocumentPage({ id }: { id: string }) {
  const [params] = useSearchParams();
  return params.get('demo') ? <DemoDocument doc={id} /> : <SpaceDocument id={id} />;
}

function SpaceDocument({ id }: { id: string }) {
  const navigate = useNavigate();
  const outline = useQuery({
    queryKey: keys.document(id),
    queryFn: () => call(api.GET('/v1/documents/{document_id}/graph', { params: { path: { document_id: id } } })),
  });
  const pages = usePages(id);
  const name = outline.data?.nodes.find((n) => kindOf(n) === 'Document')?.properties.label as string | undefined;
  if ((outline.error as { status?: number } | null)?.status === 404 || (outline.data && !outline.data.nodes.length)) return <NotFound />;
  return (
    <DocumentView name={name ?? id} graph={outline.data} graphError={outline.error as Error | null}
      demoDoc={pages.doc} sheets={pages.sheets} pagesOfNodes={pages.pagesOfNodes} crops={pages.crops}
      pagesError={pages.error as Error | null}
      actions={name && <DeleteDocument id={id} name={name} onDeleted={() => navigate('/manage')} />} />
  );
}

function DemoDocument({ doc }: { doc: string }) {
  const t = useT();
  const building = isBuilding(useBuild().data);
  const sheets = useQuery({
    queryKey: keys.sheets(doc),
    queryFn: () => call(api.GET('/v1/files/{file_id}/pages', { params: { path: { file_id: doc } } })),
    // Followed while it is read, or waits its turn in a build; at rest (done, failed, drawn only,
    // never started) there is nothing to follow.
    refetchInterval: (q) => (!q.state.error && q.state.data && (WORKING.includes(q.state.data.state) || (building && q.state.data.state === 'idle')) ? 2500
      : !q.state.data && !q.state.error ? 2500 : false),
    retry: false,
    staleTime: 0,
  });
  const landed = sheets.data?.sheets.filter((s) => s.seconds || s.error).length ?? 0;
  const graph = useQuery({
    // Asked again as each page's answer lands, and once more when the reading is done.
    queryKey: [...keys.demoGraph(doc), landed, sheets.data?.state],
    enabled: !!sheets.data?.sheets.length,
    queryFn: () => call(api.GET('/v1/files/{file_id}/graph', { params: { path: { file_id: doc } } })),
    placeholderData: (previous) => previous,
  });
  const s = sheets.data;
  if ((sheets.error as { status?: number } | null)?.status === 404) return <NotFound />;
  const took = s?.elapsed != null ? t('took', s.elapsed) : null;
  const status = !s ? null : s.state === 'error' ? { text: t('failed', reason(t, s.error ?? '')), busy: false }
    : s.state === 'cancelled' ? { text: t('cancelledRead'), busy: false }
    : WORKING.includes(s.state) ? { text: [s.progress
      ? t('readingProgress', s.progress.percent, t('stageName', s.progress.stage), s.progress.done, s.progress.total)
      : s.sheets.length ? t('readingNow', s.sheets.filter((p) => !p.pending).length, s.sheets.length)
      : t('drawingPages'), took].filter(Boolean).join(' · '), busy: true }
    : s.state === 'done' && took ? { text: t('readDone', took), busy: false } : null;
  return (
    <DocumentView name={s?.source ?? doc} graph={graph.data} graphError={(sheets.error ?? graph.error) as Error | null}
      demoDoc={doc} sheets={s} pagesOfNodes={graph.data?.pages ?? {}} crops={graph.data?.crops ?? []} pagesError={null}
      status={status} actions={<Badge tone="neutral">{t('notInGraph')}</Badge>} />
  );
}

function NotFound() {
  const t = useT();
  return (
    <Empty className="pt-24" icon={<FileQuestion className="size-5" />} title={t('docNotFound')}>
      <p>{t('docNotFoundBody')}</p>
      <Link to="/manage" className="mt-3 inline-block text-sm text-accent-fg hover:underline">{t('library')} →</Link>
    </Empty>
  );
}

function DocumentView({ name, graph, graphError, demoDoc, sheets, pagesOfNodes, crops, pagesError, status, actions }: {
  name: string;
  graph?: DocumentGraph;
  graphError: Error | null;
  demoDoc?: string;
  sheets?: Pages;
  pagesOfNodes: Record<string, number>;
  crops: string[];
  pagesError: Error | null;
  status?: { text: string; busy: boolean } | null;
  actions?: React.ReactNode;
}) {
  const t = useT();
  const [picked, setPicked] = useState<string | null>(null);
  const [page, setPage] = useState<number | null>(null);
  const format = formatOf(name);
  const pages = sheets?.sheets ?? [];
  const shown = pages.find((p) => p.index === page) ?? pages[0];

  const pick = (id: string | null) => {
    setPicked(id);
    if (id && pagesOfNodes[id] !== undefined) setPage(pagesOfNodes[id]);
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-3 border-b border-line px-6 py-4">
        <Link to="/manage" className="text-faint hover:text-fg" aria-label={t('library')}><ArrowLeft className="size-4" /></Link>
        <FileIcon file={name} className="size-5" />
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-lg font-semibold" title={shownName(name)}>{stem(name)}</h1>
          {status && <p className="flex items-center gap-1.5 text-xs text-muted">{status.busy && <Spinner className="size-3" />}{status.text}</p>}
        </div>
        {/* The far right is where "Details" was clicked: the danger button is kept off that spot. */}
        {actions}
        <span className="font-mono text-2xs text-faint uppercase">{format}</span>
      </div>
      <div className="grid min-h-0 flex-1 gap-4 p-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
        <section className="flex min-h-[520px] flex-col rounded-xl border border-line bg-panel p-3">
          <h2 className="mb-2 text-sm font-semibold">{t('outlineTitle')}</h2>
          {graphError ? <Empty icon={<AlertTriangle className="size-5" />} title={t('cannotReadOutline')}>{graphError.message}</Empty>
            : !graph ? <Skeleton className="flex-1" />
            : <OutlineGraph graph={graph} pages={pagesOfNodes} onPick={pick} frame={picked ? null : shown?.index ?? null} picked={picked} />}
        </section>
        <section className="flex min-h-[520px] flex-col rounded-xl border border-line bg-panel p-3">
          {picked && graph ? (
            <NodeDetail graph={graph} id={picked} crop={demoDoc && crops.includes(picked) && pagesOfNodes[picked] !== undefined
              ? nodeCrop(demoDoc, pagesOfNodes[picked], picked) : null} onBack={() => setPicked(null)}
              where={pagesOfNodes[picked] !== undefined ? pageNameAt(pages, pagesOfNodes[picked], format, t) : null} />
          ) : pagesError || !demoDoc || !shown ? (
            <PagesPending error={pagesError} sheets={sheets} />
          ) : (
            <>
              <div className="mb-2 flex items-center gap-2">
                <h2 className="text-sm font-semibold">{t('renderedPage')}</h2>
                <span className="font-mono text-2xs text-faint">{shown.width}×{shown.height}</span>
                {shown.seconds != null && <span className="ml-auto text-2xs text-faint">{t('doneIn', shown.seconds)}</span>}
                {shown.error && <span className="ml-auto text-2xs text-danger-fg">⚠ {clip(shown.error, 60)}</span>}
              </div>
              <div className="mb-2"><PageTabs pages={pages} current={shown.index} format={format} onPick={setPage} /></div>
              <PageViewer className="min-h-[420px] flex-1" src={pageImage(demoDoc, shown.index)} alt={pageName(shown, format, t)}
                width={shown.width} height={shown.height} />
              {shown.derived && shown.derived.length > 0 && (
                <details className="mt-2 text-xs">
                  <summary className="cursor-pointer text-muted">{t('parentsOnPage', shown.derived.length)}</summary>
                  <ul className="mt-1 max-h-40 space-y-1 overflow-y-auto">
                    {shown.derived.map((d, i) => <li key={i} className="rounded border-l-2 border-accent bg-subtle px-2 py-1"><b>{d.node}</b> → {d.to}</li>)}
                  </ul>
                </details>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
}

function NodeDetail({ graph, id, crop, where, onBack }: {
  graph: DocumentGraph; id: string; crop: string | null; where: string | null; onBack: () => void;
}) {
  const t = useT();
  const outline = useMemo(() => outlineOf(graph), [graph]);
  const n = outline.byId.get(id);
  if (!n) return null;
  const p = n.properties as Record<string, unknown>;
  const kind = kindOf(n);
  const nameOf = (x?: string) => {
    const node = x ? outline.byId.get(x) : undefined;
    return node ? clip(shownName(node.properties.text ?? node.properties.label), 60) : '—';
  };
  const chunks = (graph.chunks ?? []).map((c, i) => ({ c, i })).filter(({ c }) => c.members.includes(id));
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="mb-3 flex items-center gap-2">
        <Button variant="ghost" size="sm" onClick={onBack}><ChevronLeft className="size-3.5" />{t('backToPage')}</Button>
        {where && <span className="text-xs text-faint">{t('nodeOn', where)}</span>}
      </div>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto">
        <div className="flex items-center gap-2">
          <span className="rounded-full px-2 py-0.5 text-2xs font-semibold text-[#1F2125]" style={{ background: colorOf(kind) }}>:{kind}</span>
          <h2 className="min-w-0 truncate font-semibold" title={shownName(String(p.label ?? ''))}>{clip(shownName(String(p.label ?? '')), 140)}</h2>
        </div>
        {crop && <img src={crop} alt="" className="max-h-72 rounded-lg border border-line object-contain" />}
        {typeof p.text === 'string' && p.text && (
          <pre className="max-h-64 overflow-auto rounded-lg border border-line bg-subtle p-3 font-sans text-sm leading-6 whitespace-pre-wrap">{p.text}</pre>
        )}
        <table className="w-full text-xs">
          <tbody className="divide-y divide-line">
            <tr><td className="py-1 pr-3 whitespace-nowrap text-faint">{t('outlineParent')}</td><td className="py-1">{nameOf(outline.parent(id))}</td></tr>
            <tr><td className="py-1 pr-3 whitespace-nowrap text-faint">{t('heldIn')}</td><td className="py-1">{nameOf(outline.owner.get(id))}</td></tr>
            {chunks.length > 0 && (
              <tr><td className="py-1 pr-3 whitespace-nowrap text-faint">{t('chunks')}</td>
                <td className="py-1">{chunks.map(({ c, i }) => <div key={i}>{t('chunkOf', i + 1, c.importance == null ? null : c.importance.toFixed(2))}</div>)}</td></tr>
            )}
          </tbody>
        </table>
        {/* The rest is what the graph stores about the node -- for whoever debugs it, folded. */}
        <details className="text-xs">
          <summary className="cursor-pointer text-muted">{t('allProperties', Object.keys(p).length)}</summary>
          <table className="mt-1 w-full"><tbody className="divide-y divide-line">
            {Object.entries(p).filter(([k]) => k !== 'text').map(([k, v]) => (
              <tr key={k}><td className="py-1 pr-3 align-top whitespace-nowrap text-faint">{k}</td>
                <td className="py-1 break-all">{clip(Array.isArray(v) ? v.join(', ') : String(v), 300)}</td></tr>
            ))}
          </tbody></table>
        </details>
      </div>
    </div>
  );
}
