// What a question found, as folders: the documents of the passages found are the top folders in
// order of priority (hop 1); inside each, the documents linked to it (hop 2); inside those,
// theirs (hop 3). Each row says how it is tied to the one above it and why it is here, and the
// tree is a tree to the keyboard too (WAI-ARIA tree: arrows, Home/End, Enter).
import { ChevronRight, Quote } from 'lucide-react';
import { memo, useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { FileIcon, HopBadge, RelationChip } from '../../components/docs';
import { changedAt, cn, formatOf, ownText, shownName, stem } from '../../lib/format';
import { useLang, useT } from '../../lib/i18n';
import type { Trail, TrailEntry } from '../../lib/hops';

/** A row as drawn: where it is in the tree (`key`, the ids down to it -- one document can stand
 * in several folders), how deep, and the row above it. */
export interface Row { key: string; entry: TrailEntry; level: 1 | 2 | 3; parent?: Row; index: number }

/** A row's key: the ids down to it -- one document can stand in several folders. */
const rowKey = (parent: Row | undefined, id: string) => (parent ? `${parent.key}/${id}` : id);

/** The row where a document first stands in the folders -- the shallowest, then the first --
 * with its folders above it. */
export function firstRow(trail: Trail, docId: string): Row | null {
  let level: { entry: TrailEntry; row: Row }[] = trail.rows.map((entry, index) => ({
    entry, row: { key: rowKey(undefined, entry.doc.id), entry, level: 1, index },
  }));
  for (let depth = 1; level.length && depth <= 3; depth++) {
    const found = level.find((l) => l.entry.doc.id === docId);
    if (found) return found.row;
    level = level.flatMap(({ entry, row }) => entry.next.map((next, index) => ({
      entry: next, row: { key: rowKey(row, next.doc.id), entry: next, level: (depth + 1) as 2 | 3, parent: row, index },
    })));
  }
  return null;
}

/** The words of a passage that say why its document is here: the passage without the file and
 * headings it opens with. */
export function snippet(text: string, file: string) {
  const lines = ownText(text).split('\n').map((l) => l.trim()).filter(Boolean);
  const bare = (s: string) => s.replace(/[\s_]+/g, '');
  if (lines.length > 1 && bare(lines[0]).includes(bare(stem(file)))) lines.shift();
  // A heading path ending in the heading the passage opens with says it twice.
  return lines.filter((line, i) => bare(line) !== bare(lines[i - 1] ?? '')).join(' ');
}

/** Opened by default: the first few found documents show what is linked to them; deeper folders
 * start closed, with how much they hold. */
const OPEN_AT_FIRST = 3;

function rowsOf(trail: Trail, open: Map<string, boolean>, shown: (e: TrailEntry) => boolean) {
  const rows: Row[] = [];
  const walk = (entries: TrailEntry[], level: 1 | 2 | 3, parent?: Row) => {
    entries.forEach((entry, index) => {
      if (!shown(entry)) return;
      const row: Row = { key: rowKey(parent, entry.doc.id), entry, level, parent, index };
      rows.push(row);
      if (entry.next.length && isOpen(open, row)) walk(entry.next, (level + 1) as 2 | 3, row);
    });
  };
  walk(trail.rows, 1);
  return rows;
}

const isOpen = (open: Map<string, boolean>, row: Row) =>
  open.get(row.key) ?? (row.level === 1 && row.index < OPEN_AT_FIRST);

const under = (entry: TrailEntry): number => entry.next.reduce((n, e) => n + 1 + under(e), 0);

/** Drawn again only when what it shows changes -- the selection, the folders, a citation
 * hovered (`lit`) -- not when the results around it are redrawn for anything else. */
export const ResultTree = memo(function ResultTree({ trail, selected, onSelect, cited, lit, places, formats, expandAll }: {
  trail: Trail;
  selected: string | null;
  onSelect: (row: Row) => void;
  /** Documents the answer cites. */
  cited: Set<string>;
  /** A document pointed at elsewhere -- a citation hovered. */
  lit: string | null;
  places: Map<string, number>;
  /** Formats shown; empty is all. */
  formats: Set<string>;
  /** Bumped to open (+n) or close (-n) every folder. */
  expandAll: number;
}) {
  const t = useT();
  const { lang } = useLang();
  const [open, setOpen] = useState(new Map<string, boolean>());
  const [focused, setFocused] = useState<string | null>(null);
  const items = useRef(new Map<string, HTMLDivElement>());

  useEffect(() => {
    if (!expandAll) return;
    const all = new Map<string, boolean>();
    const walk = (entries: TrailEntry[], prefix: string) => entries.forEach((e) => {
      const key = prefix ? `${prefix}/${e.doc.id}` : e.doc.id;
      if (e.next.length) { all.set(key, expandAll > 0); walk(e.next, key); }
    });
    walk(trail.rows, '');
    setOpen(all);
  }, [expandAll, trail]);

  // A row whose own format is filtered out still stands, faded, while something under it shows.
  const fits = (e: TrailEntry) => !formats.size || formats.has(formatOf(e.doc.label));
  const shown = (e: TrailEntry): boolean => fits(e) || e.next.some(shown);
  const rows = useMemo(() => rowsOf(trail, open, shown), [trail, open, formats]);

  // A document picked from the answer is shown where it stands: its folders opened.
  useEffect(() => {
    if (!selected) return;
    const above = selected.split('/').slice(0, -1).map((_, i, ids) => ids.slice(0, i + 1).join('/'));
    if (above.length) setOpen((m) => above.every((k) => m.get(k)) ? m : new Map([...m, ...above.map((k) => [k, true] as const)]));
  }, [selected]);
  // Scrolled to once when it is picked -- not again each time another folder opens.
  const scrolled = useRef<string | null>(null);
  useEffect(() => {
    if (!selected || scrolled.current === selected) return;
    const el = items.current.get(selected);
    if (el) { el.scrollIntoView({ block: 'nearest' }); scrolled.current = selected; }
  }, [selected, rows]);

  useEffect(() => {
    if (focused) items.current.get(focused)?.focus();
  }, [focused]);

  const toggle = (row: Row, to?: boolean) =>
    setOpen((m) => new Map(m).set(row.key, to ?? !isOpen(m, row)));

  const keyDown = (e: KeyboardEvent, row: Row) => {
    const at = rows.indexOf(row);
    const go = (r?: Row) => { if (r) { e.preventDefault(); setFocused(r.key); } };
    switch (e.key) {
      case 'ArrowDown': return go(rows[at + 1]);
      case 'ArrowUp': return go(rows[at - 1]);
      case 'Home': return go(rows[0]);
      case 'End': return go(rows[rows.length - 1]);
      case 'ArrowRight':
        e.preventDefault();
        if (row.entry.next.length && !isOpen(open, row)) toggle(row, true);
        else if (rows[at + 1]?.parent === row) setFocused(rows[at + 1].key);
        return;
      case 'ArrowLeft':
        e.preventDefault();
        if (row.entry.next.length && isOpen(open, row)) toggle(row, false);
        else if (row.parent) setFocused(row.parent.key);
        return;
      case 'Enter':
      case ' ':
        e.preventDefault();
        onSelect(row);
        return;
    }
  };

  // One row takes Tab: the focused one while it is shown, else the picked one, else the first.
  const shownKey = (key: string | null) => (key && rows.some((r) => r.key === key) ? key : null);
  const tabStop = shownKey(focused) ?? shownKey(selected) ?? rows[0]?.key;

  return (
    <div role="tree" aria-label={t('resultsTree')} className="flex flex-col gap-px">
      {rows.map((row) => {
        const { entry, level } = row;
        const { doc } = entry;
        const holds = entry.next.length > 0;
        const opened = holds && isOpen(open, row);
        const why = doc.passages[0] ? snippet(doc.passages[0].text, doc.label) : '';
        const more = (places.get(doc.id) ?? 1) - 1;
        const on = row.key === selected;
        return (
          <div
            key={row.key}
            ref={(el) => { if (el) items.current.set(row.key, el); else items.current.delete(row.key); }}
            role="treeitem"
            aria-level={level}
            aria-expanded={holds ? opened : undefined}
            aria-selected={on}
            tabIndex={row.key === tabStop ? 0 : -1}
            onKeyDown={(e) => keyDown(e, row)}
            onFocus={() => setFocused(row.key)}
            onClick={() => onSelect(row)}
            className={cn('group relative flex cursor-pointer gap-1.5 rounded-lg py-1.5 pr-2 outline-none',
              'focus-visible:ring-2 focus-visible:ring-accent/40',
              on ? 'bg-accent-soft' : lit === doc.id ? 'bg-mark/40' : 'hover:bg-hover',
              !fits(entry) && 'opacity-45')}
            style={{ paddingLeft: 4 + (level - 1) * 24 }}
          >
            {level > 1 && <span aria-hidden className="absolute top-0 bottom-0 w-0.5 rounded-full bg-line-strong" style={{ left: 13 + (level - 2) * 24 }} />}
            <button
              tabIndex={-1}
              aria-hidden
              onClick={(e) => { e.stopPropagation(); if (holds) toggle(row); }}
              className={cn('mt-0.5 grid size-5 shrink-0 place-items-center rounded text-faint', holds ? 'hover:bg-active hover:text-fg' : 'invisible')}
            >
              <ChevronRight className={cn('size-3.5 transition-transform', opened && 'rotate-90')} />
            </button>
            <div className="min-w-0 flex-1">
              {/* The name has the first line to itself: in a narrow column the labels around it
                  left it a few letters. Every row says its hop, so the badges match the legend
                  above -- the found documents' order is their rank. */}
              <div className="flex h-6 min-w-0 items-center gap-1.5">
                <HopBadge hop={doc.hop} />
                <FileIcon file={doc.label} />
                <span className={cn('min-w-0 flex-1 truncate text-sm', on || level === 1 ? 'font-medium' : '')} title={shownName(doc.label)}>{stem(doc.label)}</span>
                <span className="shrink-0 pl-2 text-2xs text-faint" title={changedAt(doc.modified, lang, true)}>
                  {changedAt(doc.modified, lang)}
                </span>
              </div>
              <div className="flex min-w-0 flex-wrap items-center gap-1.5 pl-[22px]">
                {entry.direction && row.parent && <RelationChip direction={entry.direction} of={row.parent.entry.doc.label} />}
                <span className="shrink-0 font-mono text-2xs text-muted uppercase">{formatOf(doc.label)}</span>
                {cited.has(doc.id) && (
                  <span title={t('citedInAnswer')} className="inline-flex shrink-0 items-center gap-0.5 rounded bg-accent-soft px-1 text-2xs font-medium text-accent-fg">
                    <Quote className="size-2.5" />{t('cited')}
                  </span>
                )}
                {more > 0 && <span title={t('alsoElsewhereTip')} className="shrink-0 text-2xs text-faint">{t('alsoElsewhere', more)}</span>}
                {holds && !opened && <span className="shrink-0 rounded bg-surface px-1 font-mono text-2xs text-muted">+{under(entry)}</span>}
              </div>
              {why && <p className="line-clamp-1 pl-[22px] text-xs text-muted">{why}</p>}
            </div>
          </div>
        );
      })}
    </div>
  );
});
