// One turn of a conversation, top to bottom: the question, what it found, the answer. A turn
// being asked shows its stage (finding, writing, checking) and can be stopped; a turn asked of
// another graph than the one there is now says so and can be asked again.
import { AlertTriangle, FolderSearch, RefreshCw, Square } from 'lucide-react';
import { useCallback, useMemo, useState } from 'react';
import type { Answer } from '../../api/client';
import { Button, Empty, Skeleton, Spinner } from '../../components/ui';
import { cn } from '../../lib/format';
import { answerPath, hopTrail, type TrailDoc } from '../../lib/hops';
import { useT } from '../../lib/i18n';
import { AnswerCard } from '../search/AnswerCard';
import { firstRow, type Row } from '../search/ResultTree';
import { FoundCard } from './FoundCard';
import type { Found, Stage } from './useTurnStream';

export interface TurnShape {
  question: string;
  found?: Found | null;
  answer?: Answer | null;
  error?: string | null;
  stamp?: string | null;
  /** The documents this turn left out: asked again, they are left out again. */
  exclude?: string[] | null;
}

const STAGES = ['searching', 'answering', 'checking'] as const;

export function Turn({ turn, stage, now, compact = false, selected = null, onOpenDocument, onStop, onAskAgain, onOnly, onWithout }: {
  turn: TurnShape;
  /** Where a turn being asked is; none for one kept. */
  stage?: Stage;
  /** The graph's fingerprint now; a kept turn asked of another says so. */
  now?: string | null;
  compact?: boolean;
  /** The row of this turn shown in the preview. */
  selected?: string | null;
  /** A document picked -- `cited` when picked from the answer. */
  onOpenDocument: (row: Row, cited: boolean) => void;
  onStop?: () => void;
  onAskAgain: (question: string, documents?: number, exclude?: string[]) => void;
  onOnly?: (doc: TrailDoc) => void;
  onWithout?: (doc: TrailDoc) => void;
}) {
  const t = useT();
  const { question, found, answer } = turn;
  const error = turn.error ?? (stage === 'failed' ? t('turnUnreadable') : undefined);
  const [lit, setLit] = useState<string | null>(null);
  const trail = useMemo(() => hopTrail(found?.sources ?? []), [found]);
  const byLabel = useMemo(() => new Map(trail.nodes.map((n) => [n.label, n])), [trail]);
  const cited = useMemo(() => (answer ? answerPath(trail, answer.answer).used : new Set<string>()), [trail, answer]);
  const pick = useCallback((row: Row) => onOpenDocument(row, false), [onOpenDocument]);
  const cite = useCallback((doc: TrailDoc) => { const row = firstRow(trail, doc.id); if (row) onOpenDocument(row, true); }, [trail, onOpenDocument]);
  const hover = useCallback((doc: TrailDoc | null) => setLit(doc?.id ?? null), []);
  const exclude = turn.exclude ?? undefined;
  const again = () => onAskAgain(question, undefined, exclude);
  const live = stage === 'searching' || stage === 'answering' || stage === 'checking';
  const stale = !live && !!turn.stamp && !!now && turn.stamp !== now;

  const answerState = answer ? 'done' : error || stage === 'failed' ? 'error' : stage === 'answering' ? 'pending' : 'off';

  return (
    <article className="flex flex-col gap-3">
      <p className="ml-auto max-w-[85%] rounded-2xl rounded-br-md bg-accent-soft px-3.5 py-2 text-[15px] whitespace-pre-wrap">{question}</p>
      {stale && (
        <p role="status" className="flex items-center gap-2 rounded-lg border border-warn/30 bg-warn/5 px-3 py-1.5 text-xs">
          <AlertTriangle className="size-3.5 shrink-0 text-warn" />{t('graphChanged')}
          <button onClick={again} className="ml-auto font-medium text-accent-fg hover:underline">{t('askAgain')}</button>
        </p>
      )}
      {live && (
        <div role="status" className="flex items-center gap-1.5 text-xs text-muted">
          <Spinner className="size-3.5" />
          {STAGES.map((s, i) => (
            <span key={s} className={cn(s === stage ? 'font-medium text-fg' : STAGES.indexOf(stage) > i ? 'text-muted' : 'text-faint')}>
              {i > 0 && '→ '}{t(`stage_${s}`)}
            </span>
          ))}
          {stage !== 'answering' && onStop && (
            <Button size="sm" variant="ghost" className="ml-auto" onClick={onStop}><Square className="size-3" />{t('stop')}</Button>
          )}
        </div>
      )}
      {!found ? (
        stage === 'searching' ? (
          <div className="space-y-2">{[0, 1, 2].map((i) => <Skeleton key={i} className={i ? 'h-3 w-5/6' : 'w-1/2'} />)}</div>
        ) : error || stage === 'failed' ? (
          <Empty icon={<AlertTriangle className="size-5" />} title={t('searchFailed')}>
            <p>{error}</p>
            <Button className="mt-3" onClick={again}>{t('retry')}</Button>
          </Empty>
        ) : (
          <Empty icon={<FolderSearch className="size-5" />} title={t('searchStopped')}>
            <Button className="mt-3" onClick={again}><RefreshCw className="size-3.5" />{t('searchAgain')}</Button>
          </Empty>
        )
      ) : !trail.rows.length ? (
        <Empty icon={<FolderSearch className="size-5" />} title={t('nothingFound')}>
          <p>{t('nothingFoundBody')}</p>
          <ul className="mt-2 list-disc space-y-1 text-left">
            <li>{t('tipFewerWords')}</li>
            <li>{t('tipNumbers')}</li>
          </ul>
          <Button className="mt-3" onClick={() => onAskAgain(question, 20, exclude)}>{t('searchWider')}</Button>
        </Empty>
      ) : (
        <>
          <FoundCard trail={trail} question={question} queries={found.queries} compact={compact} selected={selected}
            onSelect={pick} cited={cited} lit={lit} onOnly={onOnly} onWithout={onWithout} />
          <AnswerCard state={answerState} answer={answer?.answer} claims={answer?.claims} checking={stage === 'checking'}
            error={error} byLabel={byLabel} onCite={cite} onHover={hover} onRetry={again} onCancel={onStop ?? (() => {})} />
        </>
      )}
    </article>
  );
}
