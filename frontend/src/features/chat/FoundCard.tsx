// What a turn found, as a card: the first few found documents with what is linked to them, and
// "show all" opening the whole tree with its format filters, folding and download. Each document
// can be previewed, asked about alone, left out of the question asked again, or opened.
import { ChevronsDownUp, ChevronsUpDown, Eye, ExternalLink, Filter, FilterX, MoreHorizontal } from 'lucide-react';
import { useCallback, useMemo, useState } from 'react';
import { Button, Menu, MenuContent, MenuItem, MenuTrigger } from '../../components/ui';
import { cn, formatOf } from '../../lib/format';
import { placesOf, type Trail, type TrailDoc } from '../../lib/hops';
import { useT } from '../../lib/i18n';
import { openOriginal } from '../manage/OpenOriginal';
import { DownloadFiles } from '../search/DownloadFiles';
import { ResultTree, type Row } from '../search/ResultTree';

/** How many found documents the card shows before "show all". */
export const TOP_FOUND = 3;
const EVERY_FORMAT = new Set<string>();

export function FoundCard({ trail, question, queries = [], compact = false, selected, onSelect, cited, lit, onOnly, onWithout, busy }: {
  trail: Trail;
  question: string;
  /** What was searched, when the question was asked as several searches. */
  queries?: string[];
  /** The first few only, with no "show all" -- a narrow window. */
  compact?: boolean;
  selected: string | null;
  onSelect: (row: Row) => void;
  cited: Set<string>;
  lit: string | null;
  onOnly?: (doc: TrailDoc) => void;
  onWithout?: (doc: TrailDoc) => void;
  /** A turn is being asked: asking about one document now would stop it. */
  busy?: string;
}) {
  const t = useT();
  const [all, setAll] = useState(false);
  const [formats, setFormats] = useState(new Set<string>());
  const [expandAll, setExpandAll] = useState(0);
  const shown = useMemo(() => (all ? trail : { ...trail, rows: trail.rows.slice(0, TOP_FOUND) }), [trail, all]);
  const places = useMemo(() => placesOf(trail), [trail]);
  const present = useMemo(() => [...new Set(trail.nodes.map((n) => formatOf(n.label)))].sort(), [trail]);
  const linked = trail.nodes.filter((n) => n.hop > 1).length;

  const actions = useCallback((row: Row, tabbable: boolean) => {
    const { doc } = row.entry;
    return (
      <Menu>
        <MenuTrigger asChild>
          <Button variant="ghost" size="icon-sm" className="size-6" tabIndex={tabbable ? 0 : -1} aria-label={t('docActions')}><MoreHorizontal className="size-3.5" /></Button>
        </MenuTrigger>
        <MenuContent>
          <MenuItem onSelect={() => onSelect(row)}><Eye className="mr-2 size-3.5" />{t('openPreview')}</MenuItem>
          {onOnly && <MenuItem disabled={!!busy} title={busy} onSelect={() => onOnly(doc)}><Filter className="mr-2 size-3.5" />{t('askOnlyThis')}</MenuItem>}
          {onWithout && <MenuItem disabled={!!busy} title={busy} onSelect={() => onWithout(doc)}><FilterX className="mr-2 size-3.5" />{t('askWithout')}</MenuItem>}
          <MenuItem onSelect={() => void openOriginal(doc.id, t)}><ExternalLink className="mr-2 size-3.5" />{t('openOriginal')}</MenuItem>
        </MenuContent>
      </Menu>
    );
  }, [t, onSelect, onOnly, onWithout, busy]);

  return (
    <section className="rounded-xl border border-line bg-panel px-2 py-2" aria-label={t('foundTitle')}>
      <header className="flex flex-wrap items-center gap-2 px-2 pb-1">
        <h3 className="text-sm font-semibold">{t('foundTitle')}</h3>
        <span className="text-xs text-muted">{t('foundSummary', trail.rows.length, linked)}</span>
        <div className="ml-auto flex flex-wrap items-center gap-1">
          {all && present.length > 1 && present.map((f) => (
            <button key={f} aria-pressed={formats.has(f)}
              onClick={() => setFormats((s) => { const n = new Set(s); if (n.has(f)) n.delete(f); else n.add(f); return n; })}
              className={cn('h-6 rounded-md border px-2 font-mono text-2xs uppercase',
                formats.has(f) ? 'border-accent bg-accent-soft text-accent-fg' : 'border-line text-muted hover:bg-hover')}>{f}</button>
          ))}
          {all && <>
            <Button variant="ghost" size="icon-sm" aria-label={t('expandAll')} title={t('expandAll')} onClick={() => setExpandAll((n) => Math.abs(n) + 1)}><ChevronsUpDown className="size-3.5" /></Button>
            <Button variant="ghost" size="icon-sm" aria-label={t('collapseAll')} title={t('collapseAll')} onClick={() => setExpandAll((n) => -(Math.abs(n) + 1))}><ChevronsDownUp className="size-3.5" /></Button>
          </>}
          <DownloadFiles docs={trail.nodes} question={question} />
        </div>
      </header>
      {queries.length > 1 && <p className="truncate px-2 pb-1 text-2xs text-faint" title={queries.join(' · ')}>{t('searchedFor', queries.join(' · '))}</p>}
      <ResultTree trail={shown} selected={selected} onSelect={onSelect} cited={cited} lit={lit}
        places={places} formats={all ? formats : EVERY_FORMAT} expandAll={expandAll} actions={actions} />
      {!compact && trail.rows.length > TOP_FOUND && (
        <button onClick={() => { setAll((a) => !a); setFormats(new Set()); }}
          className="mt-1 ml-2 text-xs font-medium text-accent-fg hover:underline">
          {all ? t('showTopFound', TOP_FOUND) : t('showAllFound', trail.rows.length)}
        </button>
      )}
    </section>
  );
}
