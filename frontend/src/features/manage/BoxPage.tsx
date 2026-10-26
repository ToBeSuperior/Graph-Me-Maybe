// Box as a source: the user's own Box app signs in read-only, the folders are browsed here a
// level at a time, and what is checked -- folders all the way down, and files -- is read into the
// graph by a build. The files stay in Box: the app fetches one only while it reads it.
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, CheckCircle2, ChevronRight, ExternalLink, FileText, Folder } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api, call, ApiError, isBuilding } from '../../api/client';
import type { components } from '../../api/schema';
import { Button, Spinner } from '../../components/ui';
import { cn } from '../../lib/format';
import { useT } from '../../lib/i18n';
import { keys, useBuild } from '../../lib/queries';
import { PageHeader } from './ManageLayout';

type BoxItem = components['schemas']['BoxItem'];
const boxKey = ['box'] as const;
const folderKey = (id: string) => ['boxFolder', id] as const;

export function BoxPage() {
  const t = useT();
  const client = useQueryClient();
  const [opening, setOpening] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const [said, setSaid] = useState<string | null>(null);
  const [at, setAt] = useState('0');
  const box = useQuery({
    queryKey: boxKey,
    queryFn: () => call(api.GET('/v1/cloud/box')),
    // While the browser is signing in, the answer lands on the server: asked until it does.
    refetchInterval: opening && !opening.startsWith('done') ? 2000 : false,
  });
  const state = box.data;
  const folder = useQuery({
    queryKey: folderKey(at),
    enabled: !!state?.connected,
    queryFn: () => call(api.GET('/v1/cloud/box/folders/{folder_id}', { params: { path: { folder_id: at } } })),
  });
  const building = isBuilding(useBuild().data);

  const act = async (run: () => Promise<unknown>) => {
    setWorking(true);
    setSaid(null);
    try {
      await run();
    } catch (error) {
      setSaid(error instanceof ApiError ? error.message : String(error));
    } finally {
      setWorking(false);
    }
  };
  const connect = () => act(async () => setOpening((await call(api.POST('/v1/cloud/box/connect'))).url));
  const disconnect = () => act(async () => {
    client.setQueryData(boxKey, await call(api.DELETE('/v1/cloud/box')));
    setOpening(null);
    setAt('0');
  });
  const build = () => act(async () => {
    client.setQueryData(keys.build, await call(api.POST('/v1/cloud/box/build')));
  });
  const pick = (items: BoxItem[], on: boolean) => act(async () => {
    const same = (a: BoxItem, b: BoxItem) => a.id === b.id && a.type === b.type;
    // Shown checked at once; the listing asked again after the save says what was kept.
    client.setQueryData(folderKey(at), folder.data && { ...folder.data, items: folder.data.items.map(
      (i) => (items.some((item) => same(i, item)) ? { ...i, picked: on } : i)) });
    const picked = (state?.picked ?? []).filter((p) => !items.some((item) => same(p, item)));
    const now = await call(api.PUT('/v1/cloud/box/picked', { body: { items: on ? [...picked, ...items] : picked } }));
    client.setQueryData(boxKey, now);
    await client.invalidateQueries({ queryKey: ['boxFolder'] });
  });
  const callback = `${window.location.origin}/v1/cloud/box/callback`;

  return (
    <div>
      <PageHeader title={t('boxTitle')} description={t('boxBody')} />
      <div className="grid max-w-2xl gap-4 p-6 text-sm">
        <BoxDrive />
        <h2 className="mt-2 text-sm font-medium">{t('boxApiTitle')}</h2>
        {!state ? <Spinner /> : !state.configured ? (
          <div className="grid gap-2 text-muted">
            <p>{t('boxNotSetUp')} <Link to="/manage/settings" className="underline">{t('keysTitle')}</Link></p>
            <ol className="list-decimal space-y-1 pl-5">
              <li>{t('boxStepApp')}</li>
              <li>{t('boxStepRedirect')} <code className="rounded bg-surface px-1 text-fg">{callback}</code></li>
              <li>{t('boxStepScope')}</li>
              <li>{t('boxStepKeys')}</li>
            </ol>
          </div>
        ) : !state.connected ? (
          <>
            <p>{t('boxNotConnected')}</p>
            <div><Button variant="primary" onClick={connect} disabled={working}>{t('boxConnect')}</Button></div>
            {opening && (
              <p className="text-muted">
                {t('boxOpened')}{' '}
                <a href={opening} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 underline">
                  {t('driveOpenLink')}<ExternalLink className="size-3" />
                </a>
              </p>
            )}
          </>
        ) : (
          <>
            <p className="flex items-center gap-2">
              <CheckCircle2 className="size-4 text-ok" />{t('boxPicked', state.picked.length)}
            </p>
            <div data-testid="box-api" className="grid gap-2">
              <FolderList listing={folder.data} loading={folder.isLoading} disabled={working}
                isPicked={(item) => item.picked} onOpen={setAt}
                onPick={(item, on) => void pick([{ type: item.type, id: item.id, name: item.name }], on)}
                onPickAll={(items, on) => void pick(items.map((i) => ({ type: i.type, id: i.id, name: i.name })), on)} />
            </div>
            <div className="flex flex-wrap gap-2">
              <Button variant="primary" onClick={build} disabled={working || building || !state.picked.length}>
                {t('boxBuild')}
              </Button>
              <Button variant="ghost" onClick={disconnect} disabled={working}>{t('driveDisconnect')}</Button>
            </div>
          </>
        )}
        {building && <p role="status" className="text-muted">{t('boxBuilding')}</p>}
        {said && <p role="status" className="text-muted">{said}</p>}
      </div>
    </div>
  );
}

type Listing = components['schemas']['BoxFolder'];
type Listed = Listing['items'][number];

const sizeOf = (bytes?: number | null) => bytes == null ? '' : bytes < 1e3 ? `${bytes} B`
  : bytes < 1e6 ? `${(bytes / 1e3).toFixed(0)} KB` : bytes < 1e9 ? `${(bytes / 1e6).toFixed(1)} MB` : `${(bytes / 1e9).toFixed(1)} GB`;

/** A folder shown as a file browser shows one: up, the path above it, then a table of its
 * folders and files with when each changed and how big it is. A row double-clicked (or Enter)
 * opens a folder; its checkbox picks it. A file not read here is shown, and cannot be picked. */
function FolderList({ listing, loading, disabled, isPicked, onPick, onPickAll, onOpen }: {
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
      <div className="max-h-[28rem] overflow-y-auto rounded-lg border border-line bg-panel">
        <table className="w-full table-fixed text-left text-sm">
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
function BoxDrive() {
  const t = useT();
  const client = useQueryClient();
  const [at, setAt] = useState('');
  const [checked, setChecked] = useState<string[]>([]);
  const [said, setSaid] = useState<string | null>(null);
  const building = isBuilding(useBuild().data);
  const drive = useQuery({ queryKey: ['boxDrive'], queryFn: () => call(api.GET('/v1/box-drive')) });
  const folder = useQuery({
    queryKey: ['boxDriveFolder', at],
    enabled: !!drive.data?.connected,
    queryFn: () => call(api.GET('/v1/box-drive/folders', { params: { query: { path: at } } })),
  });
  if (!drive.data) return null;
  if (!drive.data.found) return <p className="text-muted">{t('boxDriveMissing')}</p>;
  const root = drive.data.root ?? '';
  const connect = async (on: boolean) => {
    client.setQueryData(['boxDrive'], await call(on ? api.POST('/v1/box-drive/connect') : api.DELETE('/v1/box-drive')));
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
    <section data-testid="box-drive" className="grid gap-3 rounded-lg border border-line p-3">
      <h2 className="text-sm font-medium">{t('boxDriveTitle')}</h2>
      {!drive.data.connected ? (
        <>
          <p className="text-muted">{t('boxDriveAsk')} <code className="rounded bg-surface px-1 text-fg">{root}</code></p>
          <div><Button variant="primary" onClick={() => void connect(true)}>{t('boxDriveConnect')}</Button></div>
        </>
      ) : (
        <>
          <FolderList listing={folder.data} loading={folder.isLoading} disabled={building}
            isPicked={(item) => checked.includes(item.id)} onOpen={setAt}
            onPick={(item, on) => setChecked(on ? [...checked, item.id] : checked.filter((id) => id !== item.id))}
            onPickAll={(items, on) => {
              const ids = items.map((i) => i.id);
              setChecked(on ? [...checked.filter((id) => !ids.includes(id)), ...ids] : checked.filter((id) => !ids.includes(id)));
            }} />
          <div className="flex flex-wrap items-center gap-3">
            <Button variant="primary" onClick={() => void build()} disabled={building || !checked.length}>
              {t('boxDriveBuild')}
            </Button>
            {checked.length > 0 && <span className="text-muted">{t('boxDriveChecked', checked.length)}</span>}
            <Button size="sm" variant="ghost" className="ml-auto" onClick={() => void connect(false)}>{t('driveDisconnect')}</Button>
          </div>
        </>
      )}
      {said && <p role="status" className="text-muted">{said}</p>}
    </section>
  );
}
