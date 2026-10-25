// Every screen sits under one bar: where you are (Ask or Manage), what the graph holds and
// whether something is coming in, and the reader's own settings.
import { Database, FolderTree, Monitor, Moon, Network, Settings2, Sun, TriangleAlert } from 'lucide-react';
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import { isBuilding } from '../api/client';
import { cn } from '../lib/format';
import { useLang, useT, type Lang } from '../lib/i18n';
import { useBuild, useBuildFollower, useCorpusMap, useSettings } from '../lib/queries';
import { useTheme, type Theme } from '../lib/theme';
import { useUpload } from '../features/upload/upload';
import { Button, Menu, MenuContent, MenuLabel, MenuRadio, MenuRadioGroup, MenuSeparator, MenuTrigger, Spinner, Tip } from './ui';

function StatusPill() {
  const t = useT();
  const navigate = useNavigate();
  const { data: map, isError, isPending } = useCorpusMap();
  const { data: build } = useBuild();
  const { uploading } = useUpload();
  const links = map ? map.links.length + map.similar.length : 0;
  // The first map waits on the graph DB, which on a first run is still being pulled and started.
  const starting = !map && isPending;
  const busy = !!uploading || isBuilding(build) || starting;
  return (
    <Tip label={isError ? t('dbUnreachable') : t('statusTip')}>
      <button
        onClick={() => navigate('/manage')}
        className="hidden h-7 items-center gap-2 rounded-full border border-line bg-panel px-2.5 text-xs text-muted hover:border-line-strong hover:text-fg sm:inline-flex"
      >
        {busy ? <Spinner className="size-3.5 text-accent" />
          : <span className={cn('size-1.5 rounded-full', isError ? 'bg-danger' : 'bg-ok')} />}
        {busy
          ? (starting ? t('pillDbStarting') : uploading ? t('pillUploading', uploading.done, uploading.total)
            : [build!.state === 'reading' && build!.percent != null ? t('pillBuildingPercent', build!.percent)
              : build!.total ? t('pillBuilding', build!.done, build!.total)
              : build!.looked ? t('lookingFolders', build!.looked) : t('pillLooking'),
              build!.elapsed != null && t('took', build!.elapsed)].filter(Boolean).join(' · '))
          : map ? t('pillCounts', map.total, links) : t('pillNoDb')}
      </button>
    </Tip>
  );
}

function Settings() {
  const t = useT();
  const { theme, setTheme } = useTheme();
  const { lang, setLang } = useLang();
  return (
    <Menu>
      <Tip label={t('settings')}>
        <MenuTrigger asChild>
          <Button variant="ghost" size="icon" aria-label={t('settings')}><Settings2 className="size-4" /></Button>
        </MenuTrigger>
      </Tip>
      <MenuContent>
        <MenuLabel>{t('theme')}</MenuLabel>
        <MenuRadioGroup value={theme} onValueChange={(v) => setTheme(v as Theme)}>
          <MenuRadio value="auto"><Monitor className="mr-2 size-3.5" />{t('themeAuto')}</MenuRadio>
          <MenuRadio value="light"><Sun className="mr-2 size-3.5" />{t('themeLight')}</MenuRadio>
          <MenuRadio value="dark"><Moon className="mr-2 size-3.5" />{t('themeDark')}</MenuRadio>
        </MenuRadioGroup>
        <MenuSeparator />
        <MenuLabel>{t('language')}</MenuLabel>
        <MenuRadioGroup value={lang} onValueChange={(v) => setLang(v as Lang)}>
          <MenuRadio value="ko">한국어</MenuRadio>
          <MenuRadio value="en">English</MenuRadio>
        </MenuRadioGroup>
      </MenuContent>
    </Menu>
  );
}

/** What keeps the demo from reading or answering -- a key missing or refused -- said on every
 * page but the one where it is put right. */
function NotReady() {
  const t = useT();
  const { data } = useSettings();
  const { pathname } = useLocation();
  if (!data?.problems.length || pathname === '/manage/settings') return null;
  return (
    <div role="alert" className="flex items-center gap-2 border-b border-warn/30 bg-warn/5 px-4 py-2 text-sm">
      <TriangleAlert className="size-4 shrink-0 text-warn" />
      <span className="min-w-0 truncate"><b className="font-medium">{t('notReady')}</b> · {data.problems[0]}
        {data.problems.length > 1 && ` (+${data.problems.length - 1})`}</span>
      <Link to="/manage/settings" className="ml-auto shrink-0 font-medium text-accent hover:underline">{t('openSettings')}</Link>
    </div>
  );
}

const tab = ({ isActive }: { isActive: boolean }) =>
  cn('inline-flex h-8 items-center gap-1.5 rounded-md px-2.5 text-sm font-medium transition-colors',
    isActive ? 'bg-surface text-fg' : 'text-muted hover:text-fg hover:bg-hover');

export function AppShell() {
  const t = useT();
  // How a build ended is said once, wherever the reader is.
  useBuildFollower((build) => {
    const failed = build.failed.length ? `${t('notProcessed', build.failed.length)} · ${build.failed.join(' · ')}` : undefined;
    const done = [t('buildDone', build.documents, build.embedded),
      build.elapsed != null && t('took', build.elapsed)].filter(Boolean).join(' · ');
    if (build.state === 'error') toast.error(t('buildFailed'), { description: build.error ?? '' });
    else if (build.state === 'cancelled') toast.info(t('buildCancelled', build.documents, build.skipped ?? 0), { description: failed });
    else if (!build.documents && build.failed.length) toast.error(t('buildNothingRead'), { description: failed });
    else if (!build.documents && build.known) toast.info(t('buildAllKnown', build.known));
    else if (build.failed.length) toast.warning(done, { description: failed });
    else toast.success(done,
      { description: build.known ? t('alreadyIn', build.known) : undefined });
  });
  return (
    <div className="flex h-full flex-col">
      <header className="sticky top-0 z-30 flex h-12 shrink-0 items-center gap-2 border-b border-line bg-bg/85 px-3 backdrop-blur sm:px-4">
        <NavLink to="/" className="mr-2 flex items-center gap-2 font-semibold tracking-tight">
          <span className="grid size-6 place-items-center rounded-md bg-accent text-on-accent"><Network className="size-3.5" /></span>
          <span className="hidden sm:inline">Graph-Me-Maybe</span>
        </NavLink>
        <nav className="flex items-center gap-1" aria-label={t('navigation')}>
          <NavLink to="/" end className={tab}><FolderTree className="size-4" />{t('navAsk')}</NavLink>
          <NavLink to="/manage" className={tab}><Database className="size-4" />{t('navManage')}</NavLink>
        </nav>
        <div className="ml-auto flex items-center gap-1.5">
          <StatusPill />
          <Settings />
        </div>
      </header>
      <NotReady />
      <main className="min-h-0 flex-1">
        <Outlet />
      </main>
    </div>
  );
}
