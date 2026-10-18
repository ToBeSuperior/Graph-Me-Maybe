// How a document and its ties look wherever they appear: its file icon by format, the hop it was
// reached at (a number, not only a colour), and how it is tied to the one above it.
import { ArrowLeft, ArrowRight, FileSpreadsheet, FileText, FileType2, Presentation, Waves } from 'lucide-react';
import { cn, formatOf, shownName } from '../lib/format';
import { useT } from '../lib/i18n';
import type { Direction } from '../lib/hops';

const FORMAT = {
  docx: { Icon: FileText, color: 'text-[#2b6cde]' },
  xlsx: { Icon: FileSpreadsheet, color: 'text-[#1e8a4c]' },
  pptx: { Icon: Presentation, color: 'text-[#d24726]' },
  pdf: { Icon: FileType2, color: 'text-[#d63b3b]' },
} as const;

export function FileIcon({ file, className }: { file: string; className?: string }) {
  const known = FORMAT[formatOf(file) as keyof typeof FORMAT];
  const Icon = known?.Icon ?? FileText;
  return <Icon className={cn('size-4 shrink-0', known?.color ?? 'text-muted', className)} aria-hidden />;
}

const HOP_BG = { 1: 'bg-hop-1', 2: 'bg-hop-2', 3: 'bg-hop-3' } as const;

export function HopBadge({ hop }: { hop: 1 | 2 | 3 }) {
  const t = useT();
  return (
    <span title={t(`hop${hop}`)} aria-label={t(`hop${hop}`)}
      className={cn('grid size-4 shrink-0 place-items-center rounded-full font-mono text-[10px] font-semibold text-white', HOP_BG[hop])}>
      {hop}
    </span>
  );
}

/** How the row's document is tied to the one above it, said of the row's own document: the one
 * above citing it means this one is cited. */
const AS_SEEN_BELOW = {
  cites: { word: 'relCited', tip: 'relCitedTip', Icon: ArrowLeft, color: 'text-cited bg-cited/10' },
  cited_by: { word: 'relCites', tip: 'relCitesTip', Icon: ArrowRight, color: 'text-cites bg-cites/10' },
  similar: { word: 'relSimilar', tip: 'relSimilarTip', Icon: Waves, color: 'text-similar bg-similar/10' },
} as const;

export function RelationChip({ direction, of }: { direction: Direction; of: string }) {
  const t = useT();
  const { word, tip, Icon, color } = AS_SEEN_BELOW[direction];
  return (
    <span title={t(tip, shownName(of))} className={cn('inline-flex h-5 shrink-0 items-center gap-1 rounded px-1.5 text-2xs font-medium', color)}>
      <Icon className="size-3" aria-hidden />{t(word)}
    </span>
  );
}
