// Google Drive as a source: the user's own OAuth client signs in, Google's picker grants the
// files picked, and a build reads them into the graph. The files stay in Drive -- the app fetches
// one only while it is read, drawn or downloaded.
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, ExternalLink } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api, call, ApiError, isBuilding } from '../../api/client';
import { Button, Spinner } from '../../components/ui';
import { useT } from '../../lib/i18n';
import { keys, useBuild } from '../../lib/queries';
import { PageHeader } from './ManageLayout';

const driveKey = ['googleDrive'] as const;

export function DrivePage() {
  const t = useT();
  const client = useQueryClient();
  const [opening, setOpening] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const [said, setSaid] = useState<string | null>(null);
  const drive = useQuery({
    queryKey: driveKey,
    queryFn: () => call(api.GET('/v1/cloud/google')),
    // While the browser is signing in, the answer lands on the server: asked until it does.
    refetchInterval: opening ? 2000 : false,
  });
  const state = drive.data;
  // One build at a time: while any runs, this page says so rather than asking for another.
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
  const connect = () => act(async () => {
    const { url } = await call(api.POST('/v1/cloud/google/connect'));
    setOpening(url);
  });
  const build = () => act(async () => {
    client.setQueryData(keys.build, await call(api.POST('/v1/cloud/google/build')));
  });
  const disconnect = () => act(async () => {
    client.setQueryData(driveKey, await call(api.DELETE('/v1/cloud/google')));
    setOpening(null);
  });

  return (
    <div>
      <PageHeader title={t('driveTitle')} description={t('driveBody')} />
      <div className="grid max-w-2xl gap-4 p-6 text-sm">
        {!state ? <Spinner /> : !state.configured ? (
          <p className="text-muted">
            {t('driveNotSetUp')} <Link to="/manage/settings" className="underline">{t('keysTitle')}</Link>
          </p>
        ) : (
          <>
            <p className="flex items-center gap-2">
              {state.connected ? <CheckCircle2 className="size-4 text-ok" /> : null}
              {state.connected ? t('drivePicked', state.picked) : t('driveNotConnected')}
            </p>
            <div className="flex flex-wrap gap-2">
              <Button variant="primary" onClick={connect} disabled={working}>
                {state.connected ? t('drivePickMore') : t('driveConnect')}
              </Button>
              {state.connected && (
                <>
                  <Button onClick={build} disabled={working || building || !state.picked}>
                    {t('driveBuild')}
                  </Button>
                  <Button variant="ghost" onClick={disconnect} disabled={working}>
                    {t('driveDisconnect')}
                  </Button>
                </>
              )}
            </div>
            {opening && (
              <p className="text-muted">
                {t('driveOpened')}{' '}
                <a href={opening} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 underline">
                  {t('driveOpenLink')}<ExternalLink className="size-3" />
                </a>
              </p>
            )}
          </>
        )}
        {building && <p role="status" className="text-muted">{t('driveBuilding')}</p>}
        {said && <p role="status" className="text-muted">{said}</p>}
      </div>
    </div>
  );
}
