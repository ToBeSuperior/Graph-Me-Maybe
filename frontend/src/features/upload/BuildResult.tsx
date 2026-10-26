// How the last build ended, kept after it ends: how many went in, how many failed, and each
// failure by name with its reason beside it -- a toast is gone in seconds, a folder's
// failures are read at leisure.
import { useState } from 'react';
import { type BuildState } from '../../api/client';
import { Button, Dialog, DialogContent, DialogTrigger } from '../../components/ui';
import { useT } from '../../lib/i18n';
import { reason } from '../../lib/reasons';
import { useBuild } from '../../lib/queries';

/** A failure as the server says it, `<name>: <reason>`, split at the first `: ` -- a name
 * holds none: neither macOS nor Windows lets a file or folder name hold a colon. */
export function splitFailure(failure: string): [string, string] {
  const at = failure.indexOf(': ');
  return at < 0 ? [failure, ''] : [failure.slice(0, at), failure.slice(at + 2)];
}

const ended = (build?: BuildState | null): build is BuildState =>
  !!build && build.kind !== 'embed' && (build.state === 'done' || build.state === 'cancelled');

export function BuildResult() {
  const t = useT();
  const { data: build } = useBuild();
  const [open, setOpen] = useState(false);
  if (!ended(build)) return null;
  const counts = [t('resultPut', build.documents), build.known > 0 && t('resultKnown', build.known),
    t('resultFailed', build.failed.length), build.skipped > 0 && t('resultSkipped', build.skipped),
    build.elapsed != null && t('took', build.elapsed)].filter(Boolean).join(' · ');
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <div className="rounded-xl border border-line bg-panel p-3 text-xs">
        <div className="text-faint">{t('lastBuild')}</div>
        <div className="mt-1 font-medium">{t('resultPut', build.documents)} · {t('resultFailed', build.failed.length)}</div>
        <DialogTrigger asChild>
          <Button size="sm" variant="ghost" className="mt-1 -ml-2">{t('showResult')}</Button>
        </DialogTrigger>
      </div>
      <DialogContent title={t('lastBuild')} description={counts} closeLabel={t('close')} className="w-[min(720px,calc(100vw-32px))]">
        {build.failed.length > 0 && (
          <div className="max-h-[60vh] overflow-y-auto rounded-lg border border-line">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-subtle text-left text-xs text-muted">
                <tr><th className="px-3 py-2 font-medium">{t('resultName')}</th><th className="px-3 py-2 font-medium">{t('resultWhy')}</th></tr>
              </thead>
              <tbody>
                {build.failed.map((failure, i) => {
                  const [name, why] = splitFailure(failure);
                  return (
                    <tr key={i} className="border-t border-line align-top">
                      <td className="px-3 py-2 font-medium break-all">{name}</td>
                      <td className="px-3 py-2 text-muted">{reason(t, why)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
