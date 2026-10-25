// Box as a source: the user's own Box app signs in read-only, the folders are browsed here a
// level at a time, and what is checked -- folders all the way down, and files -- is read into the
// graph by a build. The files stay in Box: the app fetches one only while it reads it.
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, ChevronRight, ExternalLink, FileText, Folder } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api, call, ApiError, isBuilding } from '../../api/client';
import type { components } from '../../api/schema';
import { Button, Spinner } from '../../components/ui';
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
  const pick = (item: BoxItem, on: boolean) => act(async () => {
    // Shown checked at once; the listing asked again after the save says what was kept.
    client.setQueryData(folderKey(at), folder.data && { ...folder.data, items: folder.data.items.map(
      (i) => (i.id === item.id && i.type === item.type ? { ...i, picked: on } : i)) });
    const picked = (state?.picked ?? []).filter((p) => !(p.id === item.id && p.type === item.type));
    const now = await call(api.PUT('/v1/cloud/box/picked', { body: { items: on ? [...picked, item] : picked } }));
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
            <FolderList listing={folder.data} loading={folder.isLoading} disabled={working}
              isPicked={(item) => item.picked} onOpen={setAt}
              onPick={(item, on) => void pick({ type: item.type, id: item.id, name: item.name }, on)} />
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

/** A folder a level at a time: the path above it to go back up, then its folders and documents,
 * each with a checkbox; a folder's name opens it. */
function FolderList({ listing, loading, disabled, isPicked, onPick, onOpen }: {
  listing?: Listing; loading: boolean; disabled: boolean;
  isPicked: (item: Listed) => boolean; onPick: (item: Listed, on: boolean) => void;
  onOpen: (id: string) => void;
}) {
  const t = useT();
  const path = [...(listing?.path ?? []), ...(listing ? [{ id: listing.id, name: listing.name }] : [])];
  return (
    <>
      <nav aria-label={t('boxPath')} className="flex flex-wrap items-center gap-1 text-muted">
        {path.map((p, i) => (
          <span key={p.id || 'top'} className="flex items-center gap-1">
            {i < path.length - 1
              ? <button type="button" className="hover:text-fg hover:underline" onClick={() => onOpen(p.id)}>{p.name}</button>
              : <span className="font-medium text-fg">{p.name}</span>}
            {i < path.length - 1 && <ChevronRight className="size-3" />}
          </span>
        ))}
      </nav>
      <ul className="divide-y divide-line rounded-lg border border-line bg-panel">
        {loading && <li className="p-3"><Spinner /></li>}
        {listing && !listing.items.length && <li className="p-3 text-muted">{t('boxEmpty')}</li>}
        {listing?.items.map((item) => (
          <li key={`${item.type}-${item.id}`} className="flex items-center gap-3 px-3 py-2">
            <input type="checkbox" aria-label={item.name} checked={isPicked(item)} disabled={disabled}
              onChange={(e) => onPick(item, e.target.checked)} />
            {item.type === 'folder' ? (
              <button type="button" className="flex min-w-0 items-center gap-2 text-left hover:underline" onClick={() => onOpen(item.id)}>
                <Folder className="size-4 shrink-0 text-accent" /><span className="truncate">{item.name}</span>
              </button>
            ) : (
              <span className="flex min-w-0 items-center gap-2">
                <FileText className="size-4 shrink-0 text-muted" /><span className="truncate">{item.name}</span>
              </span>
            )}
          </li>
        ))}
      </ul>
    </>
  );
}

/** Box Drive, Box's own desktop client, shows Box as a folder on this machine: read from there
 * no sign-in of the app's own is needed, so a company Box that has not approved the app still
 * can. What is checked is kept on this page and built in one go. */
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
    enabled: !!drive.data?.found,
    queryFn: () => call(api.GET('/v1/box-drive/folders', { params: { query: { path: at } } })),
  });
  if (!drive.data) return null;
  if (!drive.data.found) return <p className="text-muted">{t('boxDriveMissing')}</p>;
  const root = drive.data.root ?? '';
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
      <p className="text-muted">{t('boxDriveFound')} <code className="rounded bg-surface px-1 text-fg">{root}</code></p>
      <FolderList listing={folder.data} loading={folder.isLoading} disabled={building}
        isPicked={(item) => checked.includes(item.id)} onOpen={setAt}
        onPick={(item, on) => setChecked(on ? [...checked, item.id] : checked.filter((id) => id !== item.id))} />
      <div className="flex items-center gap-3">
        <Button variant="primary" onClick={() => void build()} disabled={building || !checked.length}>
          {t('boxDriveBuild')}
        </Button>
        {checked.length > 0 && <span className="text-muted">{t('boxDriveChecked', checked.length)}</span>}
      </div>
      {said && <p role="status" className="text-muted">{said}</p>}
    </section>
  );
}

