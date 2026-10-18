// Getting the graph out to Neo4j: the documents chosen, then two ways -- straight up to an Aura
// instance, or a `.dump` file for Aura's Import Database or Neo4j Desktop. The rest (an import
// bundle for one's own server, Cypher, JSON) is under Advanced. A dump and an upload run in the
// background on the demo; the page follows them until they are done.
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, ChevronDown, Cloud, Download, FileArchive, HardDriveDownload, ShieldAlert } from 'lucide-react';
import { useMemo, useState, type ReactNode } from 'react';
import { api, call, ApiError, lastExport } from '../../api/client';
import { Button, Input, Spinner } from '../../components/ui';
import { changedAt, cn, shownName } from '../../lib/format';
import { useLang, useT } from '../../lib/i18n';
import { keys, useLibrary } from '../../lib/queries';
import { PageHeader } from './ManageLayout';

const EXPORTING = ['dumping', 'uploading'];

function Card({ icon, title, body, children }: { icon: ReactNode; title: string; body: ReactNode; children: ReactNode }) {
  return (
    <section className="flex flex-col rounded-xl border border-line bg-panel p-5">
      <div className="mb-3 flex items-center gap-2.5">
        <div className="grid size-8 place-items-center rounded-lg bg-accent-soft text-accent">{icon}</div>
        <h2 className="font-semibold">{title}</h2>
      </div>
      <div className="mb-4 text-sm leading-6 text-muted">{body}</div>
      <div className="mt-auto">{children}</div>
    </section>
  );
}

export function ExportPage() {
  const t = useT();
  const client = useQueryClient();
  const { data: library } = useLibrary();
  const docs = useMemo(() => (library?.items ?? []).filter((i) => i.in_space), [library]);
  const [chosen, setChosen] = useState<Set<string> | null>(null);
  // What is chosen, among the documents there are now: one deleted since is not chosen.
  const picked = useMemo(() => {
    const ids = docs.map((d) => d.document_id!);
    return chosen ? new Set(ids.filter((id) => chosen.has(id))) : new Set(ids);
  }, [chosen, docs]);
  // Every document is said by sending none: a space of thousands listed one by one is a request
  // longer than the server takes.
  const asked = picked.size === docs.length ? null : [...picked];
  const none = !picked.size;

  const job = useQuery({
    queryKey: keys.exportJob,
    queryFn: async () => lastExport(await call(api.GET('/v1/jobs'))),
    refetchInterval: (q) => (q.state.data && EXPORTING.includes(q.state.data.state) ? 1500 : false),
    staleTime: 0,
  });
  const busy = !!job.data && EXPORTING.includes(job.data.state);
  const [said, setSaid] = useState<string | null>(null);
  const start = async (run: () => Promise<unknown>) => {
    setSaid(null);
    try { await run(); } catch (error) { setSaid(error instanceof ApiError ? error.message : String(error)); }
    client.invalidateQueries({ queryKey: keys.exportJob });
  };

  const [aura, setAura] = useState({ uri: '', user: '', password: '', confirm: false });

  // Every download is of the documents chosen.
  const chosenQuery = (asked ?? []).map((d) => `docs=${encodeURIComponent(d)}`).join('&');
  const bundle = `/v1/export/bundle?${chosenQuery}`;
  const { lang } = useLang();
  const toggle = (id: string) => setChosen((was) => {
    const n = new Set(was ?? docs.map((d) => d.document_id!));
    if (n.has(id)) n.delete(id); else n.add(id);
    return n;
  });

  return (
    <div>
      <PageHeader title={t('exportTitle')} description={t('exportBody')} />
      <div className="grid max-w-5xl gap-6 p-6">
        <section>
          <div className="mb-2 flex items-center gap-2">
            <h2 className="text-sm font-semibold">{t('exportDocsTitle')}</h2>
            <span className="text-xs text-muted">{t('exportCount', picked.size, docs.length)}</span>
            <div className="ml-auto flex gap-1">
              <Button size="sm" variant="ghost" onClick={() => setChosen(null)}>{t('selectAll')}</Button>
              <Button size="sm" variant="ghost" onClick={() => setChosen(new Set())}>{t('selectNone')}</Button>
            </div>
          </div>
          <div className="grid max-h-48 gap-0.5 overflow-y-auto rounded-xl border border-line bg-panel p-2 sm:grid-cols-2">
            {docs.map((d) => (
              <label key={d.document_id} className="flex min-w-0 cursor-pointer items-center gap-2 rounded-md px-2 py-1 text-sm hover:bg-hover">
                <input type="checkbox" className="accent-[var(--accent-9)]" checked={picked.has(d.document_id!)} onChange={() => toggle(d.document_id!)} />
                <span className="truncate" title={shownName(d.name)}>{shownName(d.name)}</span>
              </label>
            ))}
          </div>
          <p className="mt-1.5 text-xs text-faint">{t('exportDocsNote')}</p>
        </section>

        <div className="grid gap-4 md:grid-cols-2">
          <Card icon={<Cloud className="size-4" />} title={t('auraTitle')} body={t('auraBody')}>
            <form autoComplete="off" className="space-y-2" onSubmit={(e) => {
              e.preventDefault();
              const body = { ...aura, docs: asked };
              setAura((a) => ({ ...a, password: '' }));
              void start(() => call(api.POST('/v1/jobs/aura-upload', { body })));
            }}>
              <Input placeholder="neo4j+s://xxxxxxxx.databases.neo4j.io" aria-label={t('auraAddress')} spellCheck={false}
                value={aura.uri} onChange={(e) => setAura({ ...aura, uri: e.target.value })} />
              <div className="flex gap-2">
                <Input placeholder={t('auraUser')} aria-label={t('user')} spellCheck={false}
                  value={aura.user} onChange={(e) => setAura({ ...aura, user: e.target.value })} />
                <Input type="password" placeholder={t('password')} aria-label={t('password')} autoComplete="new-password"
                  value={aura.password} onChange={(e) => setAura({ ...aura, password: e.target.value })} />
              </div>
              <label className="flex items-start gap-2 rounded-lg border border-warn/30 bg-warn/5 p-2.5 text-xs leading-5">
                <input type="checkbox" className="mt-0.5" checked={aura.confirm} onChange={(e) => setAura({ ...aura, confirm: e.target.checked })} />
                <span><ShieldAlert className="mr-1 inline size-3.5 text-warn" />{t('auraConfirm')}</span>
              </label>
              <Button type="submit" variant="primary" className="w-full" disabled={none || busy || !aura.confirm || !aura.uri || !aura.password}>
                <Cloud className="size-4" />{t('uploadToAura')}
              </Button>
            </form>
          </Card>

          <Card icon={<HardDriveDownload className="size-4" />} title={t('dumpTitle')} body={t('dumpBody')}>
            <div className="flex flex-wrap gap-2">
              <Button variant="primary" disabled={none || busy} onClick={() => start(() => call(api.POST('/v1/jobs/dump', { body: { docs: asked } })))}>
                <FileArchive className="size-4" />{t('makeDump')}
              </Button>
              {/* Only a finished dump is offered, and said for what it holds: one being made, or one
                  from an earlier choice that has not been made again, is not this one. */}
              {job.data?.dump_ready && !busy && job.data.state === 'done' && job.data.kind === 'dump' && (
                <a href={`/v1/jobs/${job.data.id}/file`} className="inline-flex h-8 items-center gap-2 rounded-md border border-line px-3 text-sm font-medium hover:bg-hover">
                  <Download className="size-4" />{t('getDump')}
                  {job.data.documents != null && job.data.finished_at != null && (
                    <span className="text-xs font-normal text-muted">{t('dumpOf', job.data.documents, changedAt(job.data.finished_at, lang, true))}</span>
                  )}
                </a>
              )}
            </div>
          </Card>
        </div>

        {(said || (job.data && job.data.state !== 'idle')) && (
          <div role="status" className={cn('flex items-start gap-2 rounded-lg border px-3 py-2 text-sm',
            said || job.data?.state === 'error' ? 'border-danger-line bg-danger-soft text-danger-fg' : 'border-line bg-panel')}>
            {busy ? <Spinner className="mt-0.5" /> : job.data?.state === 'done' && !said ? <CheckCircle2 className="mt-0.5 size-4 text-ok" /> : null}
            <span className="break-words">
              {said ?? (busy ? t(job.data!.state === 'uploading' ? 'exportUploading' : 'exportDumping')
                : job.data!.state === 'error' ? t('exportFailed', job.data!.message)
                : t(job.data!.kind === 'aura-upload' ? 'exportUploaded' : 'exportDumped', job.data!.message))}
            </span>
          </div>
        )}

        <details className="group rounded-xl border border-line bg-panel">
          <summary className="flex cursor-pointer list-none items-center gap-2 px-5 py-3 text-sm font-medium">
            <ChevronDown className="size-4 transition-transform group-open:rotate-180" />{t('advanced')}
          </summary>
          <div className="grid gap-4 border-t border-line p-5 text-sm md:grid-cols-2">
            <div>
              <h3 className="font-medium">{t('bundleTitle')}</h3>
              <p className="mt-1 mb-3 text-muted">{t('bundleBody')}</p>
              <a href={none ? undefined : bundle} aria-disabled={none} className={cn('inline-flex h-8 items-center gap-2 rounded-md border border-line px-3 font-medium hover:bg-hover', none && 'pointer-events-none opacity-50')}>
                <Download className="size-4" />{t('bundle')}
              </a>
            </div>
            <div>
              <h3 className="font-medium">{t('graphOnlyTitle')}</h3>
              <p className="mt-1 mb-3 text-muted">{t('graphOnlyNote')}</p>
              <div className="flex gap-2">
                <a href={none ? undefined : `/v1/graph.cypher?${chosenQuery}`} aria-disabled={none} className="inline-flex h-8 items-center rounded-md border border-line px-3 font-medium hover:bg-hover">Cypher</a>
                <a href={none ? undefined : `/v1/graph?${chosenQuery}`} target="_blank" rel="noreferrer" aria-disabled={none} className="inline-flex h-8 items-center rounded-md border border-line px-3 font-medium hover:bg-hover">JSON</a>
              </div>
            </div>
          </div>
        </details>
      </div>
    </div>
  );
}
