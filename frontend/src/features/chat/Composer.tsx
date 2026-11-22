// The box a conversation is asked in, fixed under its turns: the scope it asks about as chips
// above the question (every document, or the documents chosen, each taken off with its ×), and
// files dropped on it becoming the scope.
import { ArrowRight, Files, Paperclip, X } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { Button } from '../../components/ui';
import { cn, shownName } from '../../lib/format';
import { useT } from '../../lib/i18n';
import { DEFAULT_K, QUESTION_CHARS, TOP_K } from '../search/SearchBox';

/** Past this many documents the scope is one chip, its names in its tip. */
const CHIPS_SHOWN = 3;

export interface ScopeChip { id: string; label: string }

/** The scope with `id` added: asked about alone when every document was. */
export const withDocument = (scope: string[] | null, id: string) => [...new Set([...(scope ?? []), id])];

/** The scope with `id` taken off; null (every document) once nothing is left. */
export function withoutDocument(scope: string[] | null, id: string) {
  const rest = (scope ?? []).filter((d) => d !== id);
  return rest.length ? rest : null;
}

export function Composer({ scope, note, busy = false, compact = false, autoFocus = false, onAsk, onRemove, onDrop }: {
  /** The documents asked about; null for every one. */
  scope: ScopeChip[] | null;
  /** Said under the chips: a build the question waits for, files held elsewhere. */
  note?: ReactNode;
  busy?: boolean;
  compact?: boolean;
  /** The cursor in the box from the start -- not on a phone, where it opens the keyboard. */
  autoFocus?: boolean;
  onAsk: (question: string, documents: number) => void;
  /** One document taken off the scope; null takes the whole scope off. */
  onRemove?: (id: string | null) => void;
  onDrop?: (data: DataTransfer) => void;
}) {
  const t = useT();
  const [text, setText] = useState('');
  const [k, setK] = useState(DEFAULT_K);
  const [over, setOver] = useState(false);
  const send = () => {
    if (!text.trim() || busy) return;
    onAsk(text.trim(), k);
    setText('');
  };
  const chip = (key: string, label: ReactNode, title: string, remove?: () => void) => (
    <span key={key} title={title} className="inline-flex h-6 max-w-56 items-center gap-1 rounded-full border border-line bg-subtle pr-1 pl-2 text-xs">
      <span className="truncate">{label}</span>
      {remove && onRemove && (
        <button type="button" aria-label={t('scopeRemove', title)} onClick={remove} className="rounded-full p-0.5 text-faint hover:bg-hover hover:text-fg">
          <X className="size-3" />
        </button>
      )}
    </span>
  );

  return (
    <form
      onSubmit={(e) => { e.preventDefault(); send(); }}
      onDragOver={onDrop && ((e) => { e.preventDefault(); setOver(true); })}
      onDragLeave={() => setOver(false)}
      onDrop={onDrop && ((e) => { e.preventDefault(); setOver(false); onDrop(e.dataTransfer); })}
      aria-label={t('composer')}
      className={cn('rounded-xl border bg-panel p-2 shadow-sm focus-within:border-accent focus-within:ring-4 focus-within:ring-accent/10',
        over ? 'border-accent bg-accent-soft' : 'border-line')}
    >
      <div className="flex flex-wrap items-center gap-1.5 pb-1.5" aria-label={t('scope')} role="group">
        <Paperclip className="size-3.5 text-faint" aria-hidden />
        {!scope ? chip('all', t('scopeAll'), t('scopeAll'))
          : scope.length > CHIPS_SHOWN ? chip('many', <><Files className="mr-1 inline size-3" />{t('scopeDocs', scope.length)}</>,
            scope.map((s) => shownName(s.label)).join('\n'), () => onRemove?.(null))
          : scope.map((s) => chip(s.id, shownName(s.label), shownName(s.label), () => onRemove?.(s.id)))}
        {onDrop && !compact && <span className="text-2xs text-faint">{t('scopeDropHint')}</span>}
      </div>
      {note && <div className="pb-1.5 text-xs text-muted">{note}</div>}
      <div className="flex items-center gap-2">
        <input
          value={text}
          autoFocus={autoFocus}
          onChange={(e) => setText(e.target.value)}
          maxLength={QUESTION_CHARS}
          placeholder={t('askFollowUp')}
          aria-label={t('askPlaceholder')}
          className="h-8 min-w-0 flex-1 bg-transparent px-1 text-sm outline-none placeholder:text-faint"
        />
        {!compact && (
          <label className="flex shrink-0 items-center gap-1 text-xs text-faint" title={t('topKTip')}>
            <span className="hidden sm:inline">{t('topK')}</span>
            <select value={k} onChange={(e) => setK(Number(e.target.value))}
              className="rounded border border-line bg-subtle px-1 py-0.5 font-mono text-xs text-muted outline-none">
              {TOP_K.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
        )}
        <Button type="submit" variant="primary" size="sm" disabled={!text.trim() || busy} aria-label={t('askSend')}>
          <ArrowRight className="size-3.5" />
        </Button>
      </div>
      {text.length >= QUESTION_CHARS && <p role="status" className="text-2xs text-danger-fg">{t('questionTooLong', QUESTION_CHARS)}</p>}
    </form>
  );
}
