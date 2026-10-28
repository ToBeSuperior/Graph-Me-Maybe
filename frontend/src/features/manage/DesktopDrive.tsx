// A folder browsed as a file browser shows one, and a cloud drive's desktop client on this
// machine browsed with it: the drive's own app keeps its files here as ordinary folders, so
// nothing signs in and no company approval is needed.
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, ChevronRight, ExternalLink, FileText, Folder } from 'lucide-react';
import { useState } from 'react';
import { api, call, ApiError, isBuilding } from '../../api/client';
import type { components } from '../../api/schema';
import { Button, Spinner } from '../../components/ui';
import { cn } from '../../lib/format';
import { useT } from '../../lib/i18n';
import { keys, useBuild } from '../../lib/queries';

type Listing = components['schemas']['BoxFolder'];
type Listed = Listing['items'][number];

const sizeOf = (bytes?: number | null) => bytes == null ? '' : bytes < 1e3 ? `${bytes} B`
  : bytes < 1e6 ? `${(bytes / 1e3).toFixed(0)} KB` : bytes < 1e9 ? `${(bytes / 1e6).toFixed(1)} MB` : `${(bytes / 1e9).toFixed(1)} GB`;

/** A folder shown as a file browser shows one: up, the path above it, then a table of its
 * folders and files with when each changed and how big it is. A row double-clicked (or Enter)
 * opens a folder; its checkbox picks it. A file not read here is shown, and cannot be picked. */
export function FolderList({ listing, loading, disabled, isPicked, onPick, onPickAll, onOpen }: {
  listing?: Listing; loading: boolean; disabled: boolean;
  isPicked: (item: Listed) => boolean; onPick: (item: Listed, on: boolean) => void;
  onPickAll: (items: Listed[], on: boolean) => void; onOpen: (id: string) => void;
}) {
  const t = useT();
  const [focused, setFocused] = useState<string | null>(null);
  const path = [...(listing?.path ?? []), ...(listing ? [{ id: listing.id, name: listing.name }] : [])];
  const pickable = (listing?.items ?? []).filter((i) => i.readable !== false);
  const all = pickable.length > 0 && pickable.every(isPicked);
  const some = !all && pickable.some(isPicked);
  const open = (item: Listed) => { if (item.type === 'folder') onOpen(item.id); };
  return (
    <div className="grid gap-2">
      <nav aria-label={t('boxPath')} className="flex min-w-0 flex-wrap items-center gap-1 text-muted">
        {path.map((p, i) => (
          <span key={p.id || 'top'} className="flex items-center gap-1">
            {i < path.length - 1
              ? <button type="button" className="hover:text-fg hover:underline" onClick={() => onOpen(p.id)}>{p.name}</button>
              : <span className="font-medium text-fg">{p.name}</span>}
            {i < path.length - 1 && <ChevronRight className="size-3" />}
          </span>
        ))}
      </nav>
      <div className="max-h-[max(20rem,calc(100vh-24rem))] overflow-y-auto rounded-lg border border-line bg-panel">
        <table className="w-full table-fixed text-left text-sm">
          {/* A fixed table takes its widths from its first row, the Back row across all four
              columns: without these the four would share the width evenly. */}
          <colgroup><col className="w-9" /><col /><col className="hidden w-32 sm:table-column" /><col className="w-24" /></colgroup>
          <thead className="sticky top-0 bg-subtle text-xs text-muted">
            {/* Going back up is always the first row, disabled at the top; Select all under it. */}
            <tr className="border-b border-line bg-panel">
              <td colSpan={4} className="px-1 py-1">
                <button type="button" aria-label={t('folderUp')} disabled={path.length < 2}
                  onClick={() => onOpen(path[path.length - 2].id)}
                  className="flex w-full items-center gap-2 rounded px-2 py-1 text-left text-sm text-fg hover:bg-hover disabled:cursor-default disabled:text-faint disabled:hover:bg-transparent">
                  <ArrowLeft className="size-4 shrink-0" />
                  <span>{path.length < 2 ? t('folderTop') : t('folderUpTo', path[path.length - 2].name)}</span>
                </button>
              </td>
            </tr>
            <tr>
              <th className="w-9 px-3 py-2">
                <input type="checkbox" aria-label={t('pickAll')} checked={all} disabled={disabled || !pickable.length}
                  ref={(box) => { if (box) box.indeterminate = some; }}
                  onChange={(e) => onPickAll(pickable, e.target.checked)} />
              </th>
              <th className="px-2 py-2 font-medium">{t('colName')}</th>
              <th className="hidden w-32 px-2 py-2 font-medium sm:table-cell">{t('colModified')}</th>
              <th className="w-24 px-3 py-2 text-right font-medium">{t('colSize')}</th>
            </tr>
          </thead>
          <tbody>
            {loading && <tr><td colSpan={4} className="p-3"><Spinner /></td></tr>}
            {listing && !listing.items.length && <tr><td colSpan={4} className="p-3 text-muted">{t('boxEmpty')}</td></tr>}
            {listing?.items.map((item) => {
              const readable = item.readable !== false;
              return (
                <tr key={`${item.type}-${item.id}`} tabIndex={0} aria-selected={focused === item.id}
                  className={cn('cursor-default select-none border-t border-line first:border-t-0 focus:outline-none',
                    focused === item.id ? 'bg-accent/10' : 'hover:bg-hover', !readable && 'text-faint')}
                  onClick={() => setFocused(item.id)} onDoubleClick={() => open(item)}
                  onKeyDown={(e) => { if (e.key === 'Enter') open(item); }}>
                  <td className="px-3 py-1.5" onClick={(e) => e.stopPropagation()} onDoubleClick={(e) => e.stopPropagation()}>
                    <input type="checkbox" aria-label={item.name} checked={readable && isPicked(item)}
                      disabled={disabled || !readable} title={readable ? undefined : t('notReadable')}
                      onChange={(e) => onPick(item, e.target.checked)} />
                  </td>
                  <td className="truncate px-2 py-1.5">
                    <span className="flex min-w-0 items-center gap-2">
                      {item.type === 'folder'
                        ? <Folder className="size-4 shrink-0 text-accent" />
                        : <FileText className={cn('size-4 shrink-0', readable ? 'text-muted' : 'text-faint')} />}
                      <span className="truncate">{item.name}</span>
                    </span>
                  </td>
                  <td className="hidden px-2 py-1.5 text-xs text-muted sm:table-cell">
                    {item.modified ? new Date(item.modified * 1000).toLocaleDateString() : ''}
                  </td>
                  <td className="px-3 py-1.5 text-right text-xs text-muted">{item.type === 'file' ? sizeOf(item.size) : ''}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Box Drive, Box's own desktop client, shows Box as a folder on this machine: read from there
 * no sign-in of the app's own is needed, so a company Box that has not approved the app still
 * can. It is read only once the user says so, and from then on at every start. What is checked
 * is kept on this page and built in one go. */
/** Where each drive's own desktop app is downloaded from. */
const INSTALL = {
  box: 'https://www.box.com/resources/downloads',
  google: 'https://www.google.com/drive/download/',
  onedrive: 'https://www.microsoft.com/microsoft-365/onedrive/download',
} as const;

/** A cloud drive's desktop client on this machine -- Box Drive, Google Drive for desktop --
 * asked once to connect, then browsed like a folder and built from what is checked. */
export function DesktopDrive({ kind, app }: { kind: keyof typeof INSTALL; app: string }) {
  const t = useT();
  const client = useQueryClient();
  const [at, setAt] = useState('');
  const [checked, setChecked] = useState<string[]>([]);
  const [said, setSaid] = useState<string | null>(null);
  const building = isBuilding(useBuild().data);
  const params = { params: { path: { kind } } };
  const drive = useQuery({ queryKey: ['desktopDrive', kind], queryFn: () => call(api.GET('/v1/drives/{kind}', params)) });
  // What the drive keeps here: asked again once a build ends, which gives back what it read.
  const usage = useQuery({
    queryKey: ['desktopDriveUsage', kind, building],
    enabled: !!drive.data?.connected,
    queryFn: () => call(api.GET('/v1/drives/{kind}/usage', params)),
  });
  const folder = useQuery({
    queryKey: ['desktopDriveFolder', kind, at],
    enabled: !!drive.data?.connected,
    queryFn: () => call(api.GET('/v1/drives/{kind}/folders', { params: { path: { kind }, query: { path: at } } })),
  });
  if (!drive.data) return null;
  if (!drive.data.found) {
    return (
      <section data-testid={`${kind}-drive`} className="grid gap-3 rounded-lg border border-line p-3">
        <p className="text-muted">{t('deskMissing', app)}</p>
        <div className="flex flex-wrap items-center gap-2">
          <a href={INSTALL[kind]} target="_blank" rel="noreferrer"
            className="inline-flex h-8 items-center gap-2 rounded-md bg-accent px-3 text-sm font-medium text-on-accent shadow-sm hover:bg-accent-hover">
            {t('deskInstall', app)}<ExternalLink className="size-3.5" />
          </a>
          <Button variant="ghost" busy={drive.isFetching} onClick={() => void drive.refetch()}>{t('deskRecheck')}</Button>
        </div>
      </section>
    );
  }
  const root = drive.data.root ?? '';
  const connect = async (on: boolean) => {
    client.setQueryData(['desktopDrive', kind],
      await call(on ? api.POST('/v1/drives/{kind}/connect', params) : api.DELETE('/v1/drives/{kind}', params)));
    setAt('');
    setChecked([]);
  };
  const build = async () => {
    setSaid(null);
    try {
      client.setQueryData(keys.build, await call(api.POST('/v1/jobs/build', {
        body: { paths: checked.map((id) => `${root}/${id}`), files: [], embed: true } })));
    } catch (error) {
      setSaid(error instanceof ApiError ? error.message : String(error));
    }
  };
  return (
    <section data-testid={`${kind}-drive`} className="grid gap-3 rounded-lg border border-line p-3">
      <h2 className="text-sm font-medium">{t('deskTitle', app)}</h2>
      {!drive.data.connected ? (
        <>
          <p className="text-muted">{t('deskAsk', app)} <code className="rounded bg-surface px-1 text-fg">{root}</code></p>
          <div><Button variant="primary" onClick={() => void connect(true)}>{t('deskConnect')}</Button></div>
        </>
      ) : (
        <>
          {usage.data && (
            <div className="text-xs text-muted">
              <p>{t('deskUsage', sizeOf(usage.data.bytes) || '0 B', usage.data.files)}</p>
              <p className="text-faint">{t('deskGivenBack')}</p>
            </div>
          )}
          <FolderList listing={folder.data} loading={folder.isLoading} disabled={building}
            isPicked={(item) => checked.includes(item.id)} onOpen={setAt}
            onPick={(item, on) => setChecked(on ? [...checked, item.id] : checked.filter((id) => id !== item.id))}
            onPickAll={(items, on) => {
              const ids = items.map((i) => i.id);
              setChecked(on ? [...checked.filter((id) => !ids.includes(id)), ...ids] : checked.filter((id) => !ids.includes(id)));
            }} />
          <div className="flex flex-wrap items-center gap-3">
            <Button variant="primary" onClick={() => void build()} disabled={building || !checked.length}>
              {t('deskBuild', app)}
            </Button>
            {checked.length > 0 && <span className="text-muted">{t('deskChecked', checked.length)}</span>}
            <Button size="sm" variant="ghost" className="ml-auto" onClick={() => void connect(false)}>{t('deskDisconnect')}</Button>
          </div>
        </>
      )}
      {building && <p role="status" className="text-muted">{t('deskBuilding')}</p>}
      {said && <p role="status" className="text-muted">{said}</p>}
    </section>
  );
}
