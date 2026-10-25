// Managing the graph: what is in it, how its documents are tied, getting it out, and emptying
// it -- each a page of its own under one side menu, the dangerous one last and apart.
import { Box, Cloud, Download, Files, KeyRound, Network, TriangleAlert } from 'lucide-react';
import { NavLink, Outlet } from 'react-router-dom';
import { cn } from '../../lib/format';
import { useT } from '../../lib/i18n';
import { BuildProgress } from '../upload/BuildProgress';
import { AddFolderButton } from '../upload/FolderDrop';

const item = ({ isActive }: { isActive: boolean }) =>
  cn('flex h-8 items-center gap-2 rounded-md px-2.5 text-sm transition-colors',
    isActive ? 'bg-active font-medium text-fg' : 'text-muted hover:bg-hover hover:text-fg');

export function ManageLayout() {
  const t = useT();
  return (
    <div className="grid h-full grid-rows-[auto_minmax(0,1fr)] md:grid-cols-[232px_minmax(0,1fr)] md:grid-rows-1">
      <aside className="flex flex-col gap-3 border-b border-line bg-subtle/50 p-3 md:border-r md:border-b-0">
        <AddFolderButton variant="primary" />
        <BuildProgress compact />
        <nav className="flex gap-1 overflow-x-auto md:flex-col" aria-label={t('navManage')}>
          <NavLink to="/manage" end className={item}><Files className="size-4" />{t('library')}</NavLink>
          <NavLink to="/manage/map" className={item}><Network className="size-4" />{t('mapTitle')}</NavLink>
          <NavLink to="/manage/export" className={item}><Download className="size-4" />{t('exportTitle')}</NavLink>
          <NavLink to="/manage/drive" className={item}><Cloud className="size-4" />{t('driveTitle')}</NavLink>
          <NavLink to="/manage/box" className={item}><Box className="size-4" />{t('boxTitle')}</NavLink>
          <NavLink to="/manage/settings" className={item}><KeyRound className="size-4" />{t('keysTitle')}</NavLink>
          <div className="hidden flex-1 md:block" />
          <NavLink to="/manage/danger" className={({ isActive }) => cn(item({ isActive }), 'md:mt-6', !isActive && 'text-danger-fg hover:text-danger-fg')}>
            <TriangleAlert className="size-4" />{t('dangerZone')}
          </NavLink>
        </nav>
      </aside>
      <div className="min-h-0 overflow-y-auto">
        <Outlet />
      </div>
    </div>
  );
}

export function PageHeader({ title, description, actions }: { title: string; description?: string; actions?: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3 border-b border-line px-6 pt-6 pb-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted">{description}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}
