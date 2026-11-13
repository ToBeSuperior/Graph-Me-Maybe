// Every drive a graph is read from, under one entry of the side menu as Mail is: a tab per
// drive, each its own page as before.
import { NavLink, Outlet } from 'react-router-dom';
import { cn } from '../../lib/format';
import { useT } from '../../lib/i18n';

/** The drive pages' addresses, in the order their tabs show. */
export const DRIVE_PAGES = ['/manage/drive', '/manage/onedrive', '/manage/box', '/manage/drives'];

export function DrivesLayout() {
  const t = useT();
  const tab = ({ isActive }: { isActive: boolean }) => cn('-mb-px border-b-2 px-3 py-2 text-sm transition-colors',
    isActive ? 'border-accent font-medium text-fg' : 'border-transparent text-muted hover:text-fg');
  const titles = [t('driveTitle'), t('onedriveTitle'), t('boxTitle'), t('drivesTitle')];
  return (
    <div>
      <nav aria-label={t('drivesNav')} className="flex gap-1 overflow-x-auto border-b border-line px-6 pt-3">
        {DRIVE_PAGES.map((to, i) => <NavLink key={to} to={to} className={tab}>{titles[i]}</NavLink>)}
      </nav>
      <Outlet />
    </div>
  );
}
