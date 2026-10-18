// The question box, and asking: a question goes into the address (`?q=&k=`), which is what the
// results page reads -- so a search can be kept, reloaded and shared.
import { ArrowRight, CornerDownLeft, Search } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, Kbd } from '../../components/ui';
import { cn } from '../../lib/format';
import { useT } from '../../lib/i18n';

export const TOP_K = [5, 10, 15, 20];
export const DEFAULT_K = 5;
/** The longest question the demo takes (`MAX_QUESTION_CHARS` in app.py). */
export const QUESTION_CHARS = 1000;

/** The passage count a page was asked with, or the default when it is none of the choices. */
export const topKOf = (value: string | null) => (TOP_K.includes(Number(value)) ? Number(value) : DEFAULT_K);

export function useAsk() {
  const navigate = useNavigate();
  return useCallback((question: string, topK = DEFAULT_K) => {
    const q = question.trim();
    if (q) navigate(`/?q=${encodeURIComponent(q)}&k=${topK}`);
  }, [navigate]);
}

export function SearchBox({ initial = '', topK = DEFAULT_K, large = false, autoFocus = false }: {
  initial?: string; topK?: number; large?: boolean; autoFocus?: boolean;
}) {
  const t = useT();
  const ask = useAsk();
  const [text, setText] = useState(initial);
  const [k, setK] = useState(topK);
  const box = useRef<HTMLInputElement>(null);
  // ⌘K / Ctrl+K anywhere puts the cursor in the question box.
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); box.current?.focus(); box.current?.select(); }
    };
    addEventListener('keydown', key);
    return () => removeEventListener('keydown', key);
  }, []);
  return (
    <form
      onSubmit={(e) => { e.preventDefault(); ask(text, k); }}
      className={cn('group flex items-center gap-2 rounded-xl border border-line bg-panel shadow-sm transition-shadow',
        'focus-within:border-accent focus-within:ring-4 focus-within:ring-accent/10', large ? 'h-14 px-4' : 'h-10 px-3')}
    >
      <Search className={cn('shrink-0 text-faint', large ? 'size-5' : 'size-4')} />
      <input
        ref={box}
        value={text}
        autoFocus={autoFocus}
        onChange={(e) => setText(e.target.value)}
        maxLength={QUESTION_CHARS}
        placeholder={t('askPlaceholder')}
        aria-label={t('askPlaceholder')}
        className={cn('min-w-0 flex-1 bg-transparent outline-none placeholder:text-faint', large ? 'text-lg' : 'text-sm')}
      />
      <label className="flex shrink-0 items-center gap-1 text-xs text-faint" title={t('topKTip')}>
        <span className="hidden sm:inline">{t('topK')}</span>
        <select value={k} onChange={(e) => {
          setK(Number(e.target.value));
          // On a results page the open question is asked again with it at once.
          if (initial && text.trim()) ask(text, Number(e.target.value));
        }}
          className="rounded border border-line bg-subtle px-1 py-0.5 font-mono text-xs text-muted outline-none">
          {TOP_K.map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
      </label>
      {text.length >= QUESTION_CHARS && <span role="status" className="shrink-0 text-2xs text-danger-fg">{t('questionTooLong', QUESTION_CHARS)}</span>}
      {!large && <span className="hidden md:inline"><Kbd>⌘K</Kbd></span>}
      <Button type="submit" variant="primary" size={large ? 'md' : 'sm'} disabled={!text.trim()} aria-label={t('askSend')}>
        {large ? <>{t('askSend')}<CornerDownLeft className="size-3.5" /></> : <ArrowRight className="size-3.5" />}
      </Button>
    </form>
  );
}
