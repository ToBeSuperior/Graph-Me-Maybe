// Every drive a graph is read from, under one entry of the side menu as Mail is: a tab per
// drive, each its own page. A drive's desktop app keeps it on this machine as ordinary folders,
// browsed here and built from -- no sign-in of the app's own. Box has its own page: it also
// signs in through Box's API.
import type { ComponentProps } from 'react';
import { NavLink, Outlet } from 'react-router-dom';
import { cn } from '../../lib/format';
import { useT, type WordKey } from '../../lib/i18n';
import { BoxPage } from './BoxPage';
import { DesktopDrive } from './DesktopDrive';
import { PageHeader } from './ManageLayout';

interface DriveTab { path: string; title: WordKey; body?: WordKey; note?: WordKey; drives?: ComponentProps<typeof DesktopDrive>[] }

/** The drive pages, in the order their tabs show. */
const DRIVES: DriveTab[] = [
  { path: 'drive', title: 'driveTitle', body: 'driveBody', drives: [{ kind: 'google', app: 'Google Drive' }] },
  // OneDrive's desktop app also syncs every SharePoint library.
  { path: 'onedrive', title: 'onedriveTitle', body: 'onedriveBody', note: 'onedriveSync', drives: [{ kind: 'onedrive', app: 'OneDrive' }] },
  { path: 'box', title: 'boxTitle' },
  // The drives whose desktop app syncs a folder and nothing more, all on one page.
  { path: 'drives', title: 'drivesTitle', body: 'drivesBody', drives: [
    { kind: 'dropbox', app: 'Dropbox' }, { kind: 'icloud', app: 'iCloud Drive' }, { kind: 'synology', app: 'Synology Drive' }] },
];

/** The drive pages' addresses. */
export const DRIVE_PAGES = DRIVES.map((d) => `/manage/${d.path}`);

function DrivePage({ title, body, note, drives = [] }: DriveTab) {
  const t = useT();
  return (
    <div>
      <PageHeader title={t(title)} description={body && t(body)} />
      <div className="grid gap-4 p-6 text-sm">
        {note && <p className="text-muted">{t(note)}</p>}
        {drives.map((d) => <DesktopDrive key={d.kind} {...d} />)}
      </div>
    </div>
  );
}

/** The drive pages' routes, under `/manage`. */
export const driveRoutes = DRIVES.map((d) => ({ path: d.path, element: d.drives ? <DrivePage {...d} /> : <BoxPage /> }));

export function DrivesLayout() {
  const t = useT();
  const tab = ({ isActive }: { isActive: boolean }) => cn('-mb-px border-b-2 px-3 py-2 text-sm transition-colors',
    isActive ? 'border-accent font-medium text-fg' : 'border-transparent text-muted hover:text-fg');
  return (
    <div>
      <nav aria-label={t('drivesNav')} className="flex gap-1 overflow-x-auto border-b border-line px-6 pt-3">
        {DRIVES.map((d) => <NavLink key={d.path} to={`/manage/${d.path}`} className={tab}>{t(d.title)}</NavLink>)}
      </nav>
      <Outlet />
    </div>
  );
}
