// Google Drive as a source: Google Drive for desktop keeps the drive on this machine as an
// ordinary folder, browsed here and built from -- no sign-in of the app's own.
import { useT } from '../../lib/i18n';
import { DesktopDrive } from './DesktopDrive';
import { PageHeader } from './ManageLayout';

export function DrivePage() {
  const t = useT();
  return (
    <div>
      <PageHeader title={t('driveTitle')} description={t('driveBody')} />
      <div className="grid max-w-3xl gap-4 p-6 text-sm">
        <DesktopDrive kind="google" app="Google Drive" />
      </div>
    </div>
  );
}
