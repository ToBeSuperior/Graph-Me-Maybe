// Automatic builds: the folders, drive folders and mail accounts chosen to build by themselves,
// each with its mode, what changed since its last build, what went missing and when it builds
// next. Only what is listed here is looked at; manual is the default.
import { useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Hammer, Plus, X } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { api, call, errorText } from '../../api/client';
import { Button, Empty } from '../../components/ui';
import { useT } from '../../lib/i18n';
import { keys, useWatch, useWatchFolders } from '../../lib/queries';
import type { WatchTarget } from '../../lib/watch';
import { DeleteDocument } from './DeleteDocument';
import { PageHeader } from './ManageLayout';

type Mode = WatchTarget['mode'];
type Kind = WatchTarget['kind'];
const MODES: Mode[] = ['manual', 'on_change', 'daily'];
const select = 'h-8 rounded-md border border-line bg-panel px-2 text-sm text-fg focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/20';

export function WatchPage() {
  const t = useT();
  const client = useQueryClient();
  const { data } = useWatch();
  const folders = useWatchFolders().data ?? [];
  // Only a folder the workspace was built from can be chosen, and each once.
  const offered = folders.filter((f) => !data?.some((t) => t.path === f.path));
  const [path, setPath] = useState('');
  const picked = offered.find((f) => f.path === path) ?? offered[0];

  const choose = async (target: { kind: Kind; id: string }, mode: Mode, at: string | null, newPath?: string) => {
    try {
      await call(api.PUT('/v1/watch/{kind}/{target}', {
        params: { path: { kind: target.kind, target: target.id } },
        body: { mode, at: mode === 'daily' ? at ?? '02:00' : null, path: newPath ?? null },
      }));
      client.invalidateQueries({ queryKey: keys.watch });
      return true;
    } catch (error) { toast.error(t('watchFailed'), { description: errorText(error) }); return false; }
  };
  const add = async () => {
    if (!picked) return;
    const id = crypto.randomUUID().replace(/-/g, '').slice(0, 12);
    if (await choose({ kind: picked.kind, id }, 'manual', null, picked.path)) setPath('');
  };
  const forget = async (target: WatchTarget & { kind: 'folder' | 'drive' }) => {
    try {
      await call(api.DELETE('/v1/watch/{kind}/{target}', { params: { path: { kind: target.kind, target: target.id } } }));
      client.invalidateQueries({ queryKey: keys.watch });
    } catch (error) { toast.error(t('watchFailed'), { description: errorText(error) }); }
  };
  const buildNow = async (target: WatchTarget) => {
    try {
      client.setQueryData(keys.build, await call(api.POST('/v1/watch/{kind}/{target}/build', { params: { path: { kind: target.kind, target: target.id } } })));
    } catch (error) { toast.error(t('watchBuildFailed'), { description: errorText(error) }); }
  };

  return (
    <div>
      <PageHeader title={t('watchTitle')} description={t('watchBody')} />
      <div className="space-y-4 p-6">
        {offered.length ? (
          <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); add(); }}>
            <select className={`${select} min-w-64 flex-1`} value={picked?.path} onChange={(e) => setPath(e.target.value)} aria-label={t('watchFolder')}>
              {offered.map((f) => <option key={f.path} value={f.path}>{f.path} · {t('watchKind', f.kind)}</option>)}
            </select>
            <Button type="submit"><Plus className="size-4" />{t('watchAdd')}</Button>
          </form>
        ) : !folders.length && <p className="text-sm text-muted">{t('watchNoFolders')}</p>}
        {!data?.length ? <Empty title={t('watchEmpty')} /> : (
          <div className="overflow-hidden rounded-xl border border-line bg-panel">
            <div className="grid grid-cols-[minmax(0,1fr)_110px_220px_80px_110px_72px] gap-2 border-b border-line bg-subtle px-3 py-2 text-2xs font-medium text-faint uppercase max-md:hidden">
              <span>{t('watchColTarget')}</span><span>{t('watchColKind')}</span><span>{t('watchColMode')}</span>
              <span>{t('watchColChanged')}</span><span>{t('watchColNext')}</span><span />
            </div>
            <ul className="divide-y divide-line">
              {data.map((target) => (
                <li key={`${target.kind}:${target.id}`} data-testid={`watch-${target.id}`}
                  className="grid grid-cols-[minmax(0,1fr)_110px_220px_80px_110px_72px] items-center gap-2 px-3 py-2 max-md:grid-cols-1">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium" title={target.path ?? target.name}>{target.name}</div>
                    {target.path && <div className="truncate font-mono text-2xs text-faint">{target.path}</div>}
                    {target.error && (
                      <div className="flex items-center gap-1 text-xs text-danger-fg"><AlertTriangle className="size-3.5 shrink-0" />{t('watchUnreadable', target.error)}</div>
                    )}
                    {!!target.missing?.length && (
                      <details className="text-xs text-warn" title={t('watchMissingTip')}>
                        <summary className="cursor-pointer">{t('watchMissing', target.missing.length)}</summary>
                        <ul className="mt-1 font-mono text-2xs text-muted">
                          {target.missing.map((m) => (
                            <li key={m.path} className="flex items-center gap-1">
                              <span className="truncate" title={m.path}>{m.path}</span>
                              {m.document_id && (
                                <DeleteDocument compact id={m.document_id} name={m.path.split(/[\\/]/).pop()!}
                                  onDeleted={() => client.invalidateQueries({ queryKey: keys.watch })} />
                              )}
                            </li>
                          ))}
                        </ul>
                      </details>
                    )}
                  </div>
                  <span className="text-xs text-muted">{t('watchKind', target.kind)}</span>
                  <span className="flex items-center gap-1">
                    <select className={select} value={target.mode} aria-label={`${t('watchColMode')}: ${target.name}`}
                      onChange={(e) => choose(target, e.target.value as Mode, target.at ?? null)}>
                      {MODES.map((m) => <option key={m} value={m}>{t('watchMode', m)}</option>)}
                    </select>
                    {target.mode === 'daily' && (
                      <input type="time" className={select} value={target.at ?? ''} aria-label={`${t('watchTime')}: ${target.name}`}
                        onChange={(e) => e.target.value && choose(target, 'daily', e.target.value)} />
                    )}
                  </span>
                  <span className="text-sm tabular-nums">{target.changed}</span>
                  <span className="text-xs text-muted">{target.next_build ? target.next_build.slice(11, 16) : '—'}</span>
                  <span className="flex justify-end gap-0.5">
                    <Button variant="ghost" size="icon-sm" aria-label={`${t('watchBuildNow')}: ${target.name}`} title={t('watchBuildNow')} onClick={() => buildNow(target)}>
                      <Hammer className="size-3.5" />
                    </Button>
                    {target.kind !== 'mail' && (
                      <Button variant="ghost" size="icon-sm" aria-label={`${t('watchForget')}: ${target.name}`} title={t('watchForget')} onClick={() => forget(target as WatchTarget & { kind: 'folder' | 'drive' })}>
                        <X className="size-3.5" />
                      </Button>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
