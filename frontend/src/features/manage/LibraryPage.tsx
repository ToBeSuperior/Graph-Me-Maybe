// Every document a reader can open, in one list: those in the graph first, then those a folder
// brought that are not in it (still being read, or failed). Each says where it stands and when
// its file was last changed, like a folder listing.
import { useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, ChevronRight, FileSearch, RotateCw, Search, X } from 'lucide-react';
import { toast } from 'sonner';
import { useDeferredValue, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, call, type LibraryItem } from '../../api/client';
import { FileIcon } from '../../components/docs';
import { Badge, Button, Empty, Input, Skeleton } from '../../components/ui';
import { changedAt, shownName, stem } from '../../lib/format';
import { useLang, useT, type WordKey } from '../../lib/i18n';
import { keys, useLibrary } from '../../lib/queries';
import { FolderDrop } from '../upload/FolderDrop';
import { PageHeader } from './ManageLayout';

const BUSY = ['rendering', 'grouping', 'reading', 'captioning', 'referencing'];

function stateOf(item: LibraryItem): { word: WordKey; tone: 'accent' | 'ok' | 'danger' | 'neutral' } {
  if (BUSY.includes(item.state)) return { word: 'stateReading', tone: 'accent' };
  if (item.in_space) return { word: 'stateInGraph', tone: 'ok' };
  if (item.state === 'error') return { word: 'stateFailed', tone: 'danger' };
  if (item.state === 'cancelled') return { word: 'stateCancelled', tone: 'neutral' };
  if (item.state === 'done') return { word: 'stateOutside', tone: 'neutral' };
  return { word: 'stateWaiting', tone: 'neutral' };
}

/** Where a row opens: a graph document by its id, a file only the server holds by the file's. */
export const docLink = (item: LibraryItem) =>
  item.in_space ? `/manage/docs/${encodeURIComponent(item.document_id!)}` : `/manage/docs/${item.file_id}?demo=1`;

/** A document read but not in the graph -- failed, or passed over by a cancel: read again, or
 * taken off the list. */
function OutsideActions({ item }: { item: LibraryItem }) {
  const t = useT();
  const client = useQueryClient();
  const again = async () => {
    try {
      client.setQueryData(keys.build, await call(api.POST('/v1/jobs/build', { body: { files: [item.file_id!], embed: true } })));
      client.invalidateQueries({ queryKey: keys.library });
    } catch (error) { toast.error(t('cannotBuild'), { description: (error as Error).message }); }
  };
  const remove = async () => {
    try {
      await call(api.DELETE('/v1/files/{file_id}', { params: { path: { file_id: item.file_id! } } }));
      toast(t('removed', shownName(item.name)));
      client.invalidateQueries({ queryKey: keys.library });
    } catch (error) { toast.error(t('deleteFailed'), { description: (error as Error).message }); }
  };
  return (
    <span className="flex gap-0.5">
      <Button variant="ghost" size="icon-sm" aria-label={t('readAgain')} title={t('readAgain')} onClick={again}><RotateCw className="size-3.5" /></Button>
      <Button variant="ghost" size="icon-sm" aria-label={t('removeFromList')} title={t('removeFromList')} onClick={remove}><X className="size-3.5" /></Button>
    </span>
  );
}

export function LibraryPage() {
  const t = useT();
  const { lang } = useLang();
  const { data, isPending, isError, error } = useLibrary();
  const [filter, setFilter] = useState('');
  // Typing stays quick over thousands of rows: the list follows the filter when it can.
  const deferred = useDeferredValue(filter);
  const items = useMemo(() => {
    const f = deferred.trim().toLowerCase();
    return (data?.items ?? []).filter((i) => !f || i.name.toLowerCase().includes(f));
  }, [data, deferred]);
  const groups = [
    { title: t('groupSpace'), rows: items.filter((i) => i.in_space) },
    { title: t('groupOutside'), rows: items.filter((i) => !i.in_space) },
  ].filter((g) => g.rows.length);

  return (
    <div>
      <PageHeader title={t('library')} description={data ? t('libraryCount', data.items.filter((i) => i.in_space).length, data.items.filter((i) => !i.in_space).length) : undefined}
        actions={
          <div className="relative w-64">
            <Search className="pointer-events-none absolute top-2 left-2.5 size-4 text-faint" />
            <Input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder={t('findByName')} aria-label={t('findByName')} className="pl-8" />
          </div>
        } />
      <div className="p-6">
        {data && !data.space_ok && (
          <div className="mb-4 flex items-center gap-2 rounded-lg border border-warn/40 bg-warn/10 px-3 py-2 text-sm"><AlertTriangle className="size-4 text-warn" />{t('dbUnreachable')}</div>
        )}
        {isPending ? (
          <div className="space-y-2">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-10" />)}</div>
        ) : isError ? (
          <Empty icon={<AlertTriangle className="size-5" />} title={t('dbUnreachable')}>{(error as Error).message}</Empty>
        ) : !data?.items.length ? (
          <FolderDrop large />
        ) : !items.length ? (
          <Empty icon={<FileSearch className="size-5" />} title={t('noMatch')} />
        ) : (
          groups.map((g) => (
            <section key={g.title} className="mb-6">
              <h2 className="mb-2 text-xs font-medium tracking-wide text-faint uppercase">{g.title} · {g.rows.length}</h2>
              <div className="overflow-hidden rounded-xl border border-line bg-panel">
                <div className="grid grid-cols-[minmax(0,1fr)_100px_120px_40px] gap-2 border-b border-line bg-subtle px-3 py-2 text-2xs font-medium text-faint uppercase max-sm:hidden">
                  <span>{t('colName')}</span><span>{t('colState')}</span><span>{t('colModified')}</span><span />
                </div>
                <ul className="divide-y divide-line">
                  {g.rows.map((item) => {
                    const state = stateOf(item);
                    return (
                      <li key={item.document_id ?? item.file_id} style={{ contentVisibility: 'auto', containIntrinsicSize: 'auto 41px' }} className="group grid grid-cols-[minmax(0,1fr)_100px_120px_40px] items-center gap-2 px-3 py-2 hover:bg-hover max-sm:grid-cols-[minmax(0,1fr)_auto]">
                        <Link to={docLink(item)} className="flex min-w-0 items-center gap-2">
                          <FileIcon file={item.name} />
                          <span className="truncate text-sm font-medium" title={shownName(item.name)}>{stem(item.name)}</span>
                          <span className="shrink-0 font-mono text-2xs text-faint uppercase">{item.format}</span>
                        </Link>
                        <span title={item.in_space ? undefined : t('outsideTip')}><Badge tone={state.tone}>{t(state.word)}</Badge></span>
                        <span className="text-xs text-muted max-sm:hidden" title={changedAt(item.modified, lang, true)}>{changedAt(item.modified, lang)}</span>
                        <span className="flex justify-end max-sm:hidden">
                          {/* A row opens its document, whose page is where it is deleted: a bin
                              here sat where every other row offers its details. */}
                          {item.in_space || BUSY.includes(item.state)
                            ? <Link to={docLink(item)} className="text-faint hover:text-fg" aria-label={t('openInManage')} title={t('openInManage')}><ChevronRight className="size-4" /></Link>
                            : <OutsideActions item={item} />}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              </div>
            </section>
          ))
        )}
      </div>
    </div>
  );
}
