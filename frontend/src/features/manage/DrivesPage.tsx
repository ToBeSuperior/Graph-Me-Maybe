// The drives whose desktop app syncs a folder to this machine and nothing more: each found,
// connected and browsed like the others, all on one page.
import { useT } from '../../lib/i18n';
import { DesktopDrive } from './DesktopDrive';
import { PageHeader } from './ManageLayout';

export function DrivesPage() {
  const t = useT();
  return (
    <div>
      <PageHeader title={t('drivesTitle')} description={t('drivesBody')} />
      <div className="grid gap-4 p-6 text-sm">
        <DesktopDrive kind="dropbox" app="Dropbox" />
        <DesktopDrive kind="icloud" app="iCloud Drive" />
        <DesktopDrive kind="synology" app="Synology Drive" />
      </div>
    </div>
  );
}
