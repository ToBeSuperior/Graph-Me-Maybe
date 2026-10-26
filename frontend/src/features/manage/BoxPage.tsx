// Box as a source: the user's own Box app signs in read-only, the folders are browsed here a
// level at a time, and what is checked -- folders all the way down, and files -- is read into the
// graph by a build. The files stay in Box: the app fetches one only while it reads it.
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, ExternalLink } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api, call, ApiError, isBuilding } from '../../api/client';
import type { components } from '../../api/schema';
import { Button, Spinner } from '../../components/ui';
import { useT } from '../../lib/i18n';
import { keys, useBuild } from '../../lib/queries';
import { DesktopDrive, FolderList } from './DesktopDrive';
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
      <div className="grid gap-4 p-6 text-sm">
        <DesktopDrive kind="box" app="Box Drive" />
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
