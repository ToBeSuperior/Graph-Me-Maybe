// A document's pages as tabs, named the way the document names them (sheet, slide, page); a page
// still being read or one that failed says so, and a marked page carries a dot.
import { AlertTriangle } from 'lucide-react';
import { useEffect, useRef } from 'react';
import type { PageInfo, Pages } from '../api/client';
import { clip, cn } from '../lib/format';
import { useT } from '../lib/i18n';
import { pageName } from '../lib/pages';
import { Empty, Skeleton, Spinner } from './ui';

export function PageTabs({ pages, current, format, onPick, marked }: {
  pages: PageInfo[]; current: number; format: string; onPick: (index: number) => void;
  marked?: Set<number>;
}) {
  const t = useT();
  const list = useRef<HTMLDivElement>(null);
  // The page shown is the tab in view, however it came to be shown -- a passage on page 26 of 42.
  useEffect(() => {
    list.current?.querySelector('[aria-selected="true"]')?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' });
  }, [current, pages.length]);
  if (pages.length < 2) return null;
  return (
    <div ref={list} className="flex gap-1 overflow-x-auto pb-1" role="tablist" aria-label={t('pages')}>
      {pages.map((p) => (
        <button key={p.index} role="tab" aria-selected={p.index === current} onClick={() => onPick(p.index)}
          className={cn('relative h-7 shrink-0 rounded-md border px-2.5 text-xs',
            p.index === current ? 'border-accent bg-accent-soft font-medium text-accent-fg' : 'border-line text-muted hover:bg-hover')}>
          {clip(pageName(p, format, t), 16)}{p.pending ? ' …' : p.error ? ' ⚠' : ''}
          {marked?.has(p.index) && <span className="absolute -top-0.5 -right-0.5 size-2 rounded-full bg-accent ring-2 ring-panel" title={t('hasPassage')} />}
        </button>
      ))}
    </div>
  );
}

/** A document's pages not there to show yet: why they cannot be drawn, or that they are being. */
export function PagesPending({ error, sheets }: { error: Error | null; sheets?: Pages }) {
  const t = useT();
  const why = error?.message ?? (sheets?.state === 'error' || sheets?.state === 'cancelled' ? sheets.error : null);
  if (why !== null && why !== undefined) {
    return <Empty icon={<AlertTriangle className="size-5" />} title={t('cannotDrawPages')}>{why}</Empty>;
  }
  return (
    <div className="flex flex-1 flex-col gap-2">
      <div className="flex items-center gap-2 text-xs text-muted"><Spinner className="size-3.5" />{t('drawingPages')}</div>
      <Skeleton className="flex-1" />
    </div>
  );
}
