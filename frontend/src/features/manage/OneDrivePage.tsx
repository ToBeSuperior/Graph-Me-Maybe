// OneDrive and SharePoint as a source: OneDrive's desktop app keeps the user's OneDrive and
// every SharePoint library synced on this machine as ordinary folders, browsed here and built
// from -- no sign-in of the app's own.
import { useT } from '../../lib/i18n';
import { DesktopDrive } from './DesktopDrive';
import { PageHeader } from './ManageLayout';

export function OneDrivePage() {
  const t = useT();
  return (
    <div>
      <PageHeader title={t('onedriveTitle')} description={t('onedriveBody')} />
      <div className="grid gap-4 p-6 text-sm">
        <p className="text-muted">{t('onedriveSync')}</p>
        <DesktopDrive kind="onedrive" app="OneDrive" />
      </div>
    </div>
  );
}
