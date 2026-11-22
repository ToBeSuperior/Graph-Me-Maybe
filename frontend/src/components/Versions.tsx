// A document's copies and versions, folded under it: "+N versions" opens the list, each with when
// its file was last changed and the family's latest marked. Where the shown one is not the
// latest, it says which is.
import { ChevronRight } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import type { VersionOf } from '../api/client';
import { changedAt, cn, shownName, stem } from '../lib/format';
import { useLang, useT } from '../lib/i18n';
import { FileIcon } from './docs';

export function Versions({ versions, action }: {
  versions?: VersionOf[] | null;
  /** What can be done with a version, at the end of its line -- ungrouping it, where the graph
   * is looked after. */
  action?: (version: VersionOf) => ReactNode;
}) {
  const t = useT();
  const { lang } = useLang();
  const [open, setOpen] = useState(false);
  if (!versions?.length) return null;
  const newer = versions.find((v) => v.latest);
  return (
    <div className="min-w-0" onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
      <div className="flex flex-wrap items-center gap-1.5">
        <button type="button" aria-expanded={open} title={t('versionsTip')} onClick={() => setOpen((o) => !o)}
          className="inline-flex h-5 items-center gap-0.5 rounded bg-surface px-1 text-2xs font-medium text-muted hover:bg-hover hover:text-fg">
          <ChevronRight className={cn('size-3 transition-transform', open && 'rotate-90')} aria-hidden />
          {t('versionsMore', versions.length)}
        </button>
        {newer && <span className="truncate text-2xs text-faint" title={shownName(newer.file)}>{t('latestIs', stem(newer.file))}</span>}
      </div>
      {open && (
        <ul className="mt-1 flex flex-col gap-0.5 border-l border-line pl-2" aria-label={t('versionsTip')}>
          {versions.map((v) => (
            <li key={v.document_id} className="flex min-w-0 items-center gap-1.5 text-xs">
              <FileIcon file={v.file} className="size-3.5" />
              <span className="min-w-0 flex-1 truncate" title={shownName(v.file)}>{stem(v.file)}</span>
              {v.latest && <span className="shrink-0 rounded bg-accent-soft px-1 text-2xs font-medium text-accent-fg">{t('latestMark')}</span>}
              <span className="shrink-0 text-2xs text-faint" title={changedAt(v.modified, lang, true)}>{changedAt(v.modified, lang)}</span>
              {action?.(v)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
