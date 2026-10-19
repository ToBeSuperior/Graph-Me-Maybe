// The files a question found, downloaded at once: picked one by one, a hop at a time, or all,
// and saved as one zip whose folders are the hops shown on the page.
import { Download } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Button, Dialog, DialogContent, DialogTrigger } from '../../components/ui';
import { cn, shownName } from '../../lib/format';
import type { TrailDoc } from '../../lib/hops';
import { useT } from '../../lib/i18n';
import { filesLink, groupState, toggled } from '../../lib/picked';

const HOPS = [1, 2, 3] as const;

function Box({ state, onChange, label }: {
  state: 'all' | 'some' | 'none'; onChange: (on: boolean) => void; label: string;
}) {
  const box = useRef<HTMLInputElement>(null);
  useEffect(() => { if (box.current) box.current.indeterminate = state === 'some'; }, [state]);
  return (
    <input ref={box} type="checkbox" aria-label={label} checked={state === 'all'}
      onChange={() => onChange(state !== 'all')} className="size-4 shrink-0 accent-[var(--accent)]" />
  );
}

export function DownloadFiles({ docs, question }: { docs: TrailDoc[]; question: string }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [picked, setPicked] = useState(() => new Set(docs.map((d) => d.id)));
  // A new question finds new files: all of them picked again.
  useEffect(() => setPicked(new Set(docs.map((d) => d.id))), [docs]);
  const byHop = useMemo(() => HOPS.map((hop) => docs.filter((d) => d.hop === hop)), [docs]);
  const all = docs.map((d) => d.id);
  const chosen = HOPS.flatMap((hop, i) => byHop[i].filter((d) => picked.has(d.id))
    .map((d) => ({ id: d.id, folder: t(`zipFolder${hop}`) })));

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="ghost" size="sm" disabled={!docs.length}><Download className="size-3.5" />{t('downloadFiles')}</Button>
      </DialogTrigger>
      <DialogContent title={t('downloadTitle')} description={t('downloadBody')} closeLabel={t('close')}>
        <div className="mb-2 flex gap-2">
          <Button size="sm" onClick={() => setPicked(toggled(picked, all, true))}>{t('selectAll')}</Button>
          <Button size="sm" onClick={() => setPicked(toggled(picked, all, false))}>{t('clearAll')}</Button>
        </div>
        <div className="space-y-3">
          {HOPS.map((hop, i) => byHop[i].length > 0 && (
            <section key={hop} aria-label={t(`zipFolder${hop}`)}>
              <label className="flex items-center gap-2 border-b border-line py-1.5 text-sm font-medium">
                <Box state={groupState(picked, byHop[i].map((d) => d.id))} label={t(`zipFolder${hop}`)}
                  onChange={(on) => setPicked(toggled(picked, byHop[i].map((d) => d.id), on))} />
                <span className={cn('grid size-4 place-items-center rounded-full font-mono text-[10px] text-white', `bg-hop-${hop}`)}>{hop}</span>
                {t(`zipFolder${hop}`)}
                <span className="ml-auto text-xs font-normal text-muted">{byHop[i].filter((d) => picked.has(d.id)).length}/{byHop[i].length}</span>
              </label>
              <ul className="mt-1 space-y-0.5">
                {byHop[i].map((d) => (
                  <li key={d.id}>
                    <label className="flex items-center gap-2 rounded-md px-1 py-1 text-sm hover:bg-hover">
                      <Box state={picked.has(d.id) ? 'all' : 'none'} label={shownName(d.label)}
                        onChange={(on) => setPicked(toggled(picked, [d.id], on))} />
                      <span className="truncate">{shownName(d.label)}</span>
                    </label>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <Button onClick={() => setOpen(false)}>{t('cancel')}</Button>
          {chosen.length ? (
            <a href={filesLink(chosen, question)} download onClick={() => setOpen(false)}
              className="inline-flex h-8 items-center gap-2 rounded-md bg-accent px-3 text-sm font-medium text-white hover:opacity-90">
              <Download className="size-3.5" />{t('downloadPicked', chosen.length)}
            </a>
          ) : <Button disabled>{t('downloadPicked', 0)}</Button>}
        </div>
      </DialogContent>
    </Dialog>
  );
}
