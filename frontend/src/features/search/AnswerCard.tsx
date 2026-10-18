// The answer written from what was found, above the folders it was written from. It opens short:
// a generated answer is a lead to the documents, not a substitute for them, so each [file] it
// cites is a link that shows that document in the folders and its page on the right.
import { AlertTriangle, ChevronDown, RotateCw, Sparkles, Square } from 'lucide-react';
import { useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Button, Skeleton } from '../../components/ui';
import { cn, shownName } from '../../lib/format';
import { useT } from '../../lib/i18n';
import type { TrailDoc } from '../../lib/hops';
import { blocks, inlines } from '../../lib/markdown';

/** A line of the answer: bold, italics and code as they are written, and each [file] it cites a
 * button when that file was found. */
function written(text: string, byLabel: Map<string, TrailDoc>, cite: (doc: TrailDoc) => void,
  hover: (doc: TrailDoc | null) => void): ReactNode[] {
  return inlines(text).map((part, n) => {
    if (part.kind === 'strong') return <strong key={n}>{shownName(part.text)}</strong>;
    if (part.kind === 'em') return <em key={n}>{shownName(part.text)}</em>;
    if (part.kind === 'code') return <code key={n} className="rounded bg-subtle px-1 font-mono text-[0.9em]">{part.text}</code>;
    if (part.kind === 'text') return shownName(part.text);
    const doc = byLabel.get(part.text.trim());
    return doc ? (
      <button key={n} onClick={() => cite(doc)} onMouseEnter={() => hover(doc)} onMouseLeave={() => hover(null)}
        className="mx-0.5 inline-flex max-w-64 items-baseline truncate rounded bg-accent-soft px-1.5 align-baseline text-xs font-medium text-accent-fg hover:bg-accent hover:text-on-accent">
        {shownName(part.text)}
      </button>
    ) : <span key={n} className="text-faint">[{shownName(part.text)}]</span>;
  });
}

const HEADING_SIZE = ['text-lg', 'text-base', 'text-[15px]'];

/** The answer's blocks as the page draws them. */
function drawn(text: string, inline: (part: string) => ReactNode[]): ReactNode[] {
  return blocks(text).map((block, key) => {
    switch (block.kind) {
      case 'h':
        return <p key={key} role="heading" aria-level={block.level + 2}
          className={cn('mt-3 mb-1 font-semibold first:mt-0', HEADING_SIZE[Math.min(block.level, 3) - 1])}>{inline(block.text)}</p>;
      case 'ul':
      case 'ol': {
        const List = block.kind;
        return <List key={key} className={cn('my-1 space-y-0.5 pl-5', block.kind === 'ul' ? 'list-disc' : 'list-decimal')}>
          {block.items.map((item, i) => <li key={i}>{inline(item)}</li>)}
        </List>;
      }
      case 'table':
        return (
          <div key={key} className="my-2 overflow-x-auto">
            <table className="w-full border-collapse text-sm leading-6">
              {block.head && <thead><tr>{block.head.map((c, k) => <th key={k} className="border border-line bg-subtle px-2 py-1 text-left font-semibold">{inline(c)}</th>)}</tr></thead>}
              <tbody>{block.rows.map((row, r) => <tr key={r}>{row.map((c, k) => <td key={k} className="border border-line px-2 py-1 align-top">{inline(c)}</td>)}</tr>)}</tbody>
            </table>
          </div>
        );
      case 'hr':
        return <hr key={key} className="my-3 border-line" />;
      default:
        return <p key={key} className="whitespace-pre-wrap">{inline(block.text)}</p>;
    }
  });
}

export function AnswerCard({ state, answer, error, byLabel, onCite, onHover, onRetry, onCancel }: {
  state: 'off' | 'pending' | 'done' | 'error';
  answer?: string;
  error?: string;
  byLabel: Map<string, TrailDoc>;
  onCite: (doc: TrailDoc) => void;
  onHover: (doc: TrailDoc | null) => void;
  onRetry: () => void;
  onCancel: () => void;
}) {
  const t = useT();
  const [more, setMore] = useState(false);
  const [long, setLong] = useState(false);
  const body = useRef<HTMLDivElement>(null);
  // Taller than the folded height (max-h-28, 112px) is long, whether it is folded now or not.
  useLayoutEffect(() => {
    setMore(false);
    if (body.current) setLong(body.current.scrollHeight > 116);
  }, [answer]);
  const shown = useMemo(() => (answer === undefined ? null
    : drawn(answer.trim(), (part) => written(part, byLabel, onCite, onHover))), [answer, byLabel, onCite, onHover]);

  return (
    <section className="rounded-xl border border-line bg-panel" aria-live="polite">
      <header className="flex items-center gap-2 px-4 pt-3 pb-1">
        <Sparkles className="size-4 text-accent" />
        <h2 className="text-sm font-semibold">{t('answerTitle')}</h2>
        <span className="text-xs text-faint">{t('answerNote')}</span>
        <div className="ml-auto">
          {state === 'pending' && <Button size="sm" variant="ghost" onClick={onCancel}><Square className="size-3" />{t('stop')}</Button>}
          {(state === 'error' || state === 'off') && <Button size="sm" variant="ghost" onClick={onRetry}><RotateCw className="size-3" />{t(state === 'off' ? 'writeAnswer' : 'retry')}</Button>}
        </div>
      </header>
      <div className="px-4 pb-3">
        {state === 'pending' && (
          <div className="space-y-2 py-1">
            <div className="text-xs text-muted">{t('writingAnswer')}</div>
            <Skeleton className="w-full" /><Skeleton className="w-11/12" /><Skeleton className="w-2/3" />
          </div>
        )}
        {state === 'error' && (
          <p className="flex items-start gap-2 text-sm text-danger-fg"><AlertTriangle className="mt-0.5 size-4 shrink-0" />{error}</p>
        )}
        {state === 'off' && <p className="text-sm text-muted">{t('answerStopped')}</p>}
        {state === 'done' && answer !== undefined && (
          <>
            <div ref={body} className={cn('prose-answer text-[15px] leading-7 break-words', !more && 'max-h-28 overflow-hidden')}>
              {shown}
            </div>
            {long && (
              <button onClick={() => setMore((m) => !m)} className="mt-1 inline-flex items-center gap-1 text-xs font-medium text-accent-fg hover:underline">
                {t(more ? 'showLess' : 'showMore')}<ChevronDown className={cn('size-3 transition-transform', more && 'rotate-180')} />
              </button>
            )}
          </>
        )}
      </div>
    </section>
  );
}
