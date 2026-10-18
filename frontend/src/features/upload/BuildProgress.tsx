// How far a folder has come: uploaded, read, linked, embedded -- one step after another, each
// with its count, so a build of minutes never looks stuck.
import { useQueryClient } from '@tanstack/react-query';
import { Check, X } from 'lucide-react';
import { useState } from 'react';
import { api, call, isBuilding, type BuildState } from '../../api/client';
import { Button, Spinner } from '../../components/ui';
import { cn } from '../../lib/format';
import { useT, type WordKey } from '../../lib/i18n';
import { keys, useBuild } from '../../lib/queries';
import { useUpload } from './upload';

const STEPS: { key: string; word: WordKey }[] = [
  { key: 'upload', word: 'stepUpload' },
  { key: 'reading', word: 'stepReading' },
  { key: 'linking', word: 'stepLinking' },
  { key: 'embedding', word: 'stepEmbedding' },
];

/** Which step is under way, 0-3, and how far it is. */
function stepOf(uploading: { done: number; total: number } | null, build?: BuildState | null) {
  if (uploading) return { at: 0, done: uploading.done, total: uploading.total };
  if (!build || !isBuilding(build)) return null;
  const at = STEPS.findIndex((s) => s.key === build.state);
  return { at, done: build.done, total: build.total };
}

/** True while anything is coming in. */
export function useIncoming() {
  const { uploading } = useUpload();
  const { data } = useBuild();
  return !!uploading || isBuilding(data);
}

/** Stop a long build: what is in the graph stays, the rest is skipped. Embedding what was put
 * in is short and is let finish, so the graph stays searchable. */
function CancelBuild({ id, compact }: { id: string; compact: boolean }) {
  const t = useT();
  const client = useQueryClient();
  const [busy, setBusy] = useState(false);
  return (
    <Button size="sm" variant="ghost" className={cn('mt-2', !compact && 'float-right')} busy={busy} title={t('cancelBuildTip')}
      onClick={async () => {
        setBusy(true);
        try { client.setQueryData(keys.build, await call(api.POST('/v1/jobs/{job_id}/cancel', { params: { path: { job_id: id } } }))); } finally { setBusy(false); }
      }}>
      <X className="size-3.5" />{t('cancelBuild')}
    </Button>
  );
}

export function BuildProgress({ compact = false }: { compact?: boolean }) {
  const t = useT();
  const { uploading } = useUpload();
  const { data: build } = useBuild();
  const step = stepOf(uploading, build);
  if (!step) return null;
  const pct = step.total ? Math.round((step.done / step.total) * 100) : 0;
  return (
    <div className={cn('rounded-xl border border-line bg-panel', compact ? 'p-3' : 'p-4')} role="status" aria-live="polite">
      <ol className={cn('flex gap-1', compact ? 'flex-col' : 'flex-wrap items-center')}>
        {STEPS.map((s, i) => (
          <li key={s.key} className="flex items-center gap-2 text-sm">
            <span className={cn('grid size-5 place-items-center rounded-full text-2xs font-semibold',
              i < step.at ? 'bg-ok text-white' : i === step.at ? 'bg-accent text-on-accent' : 'bg-surface text-faint')}>
              {i < step.at ? <Check className="size-3" /> : i === step.at ? <Spinner className="size-3 text-on-accent" /> : i + 1}
            </span>
            <span className={cn(i === step.at ? 'font-medium text-fg' : 'text-muted')}>{t(s.word)}</span>
            {!compact && i < STEPS.length - 1 && <span className="mx-1 h-px w-6 bg-line" />}
          </li>
        ))}
      </ol>
      {step.total > 0 && (
        <div className="mt-3">
          <div className="h-1.5 overflow-hidden rounded-full bg-surface">
            <div className="h-full rounded-full bg-accent transition-[width] duration-500" style={{ width: `${pct}%` }} />
          </div>
          <div className="mt-1.5 flex justify-between gap-2 text-xs text-muted">
            <span className="truncate">{uploading ? uploading.current : t('buildCount', step.done, step.total)}</span>
            <span className="font-mono">{step.done}/{step.total}</span>
          </div>
        </div>
      )}
      {!uploading && step.at === 1 && !step.total && <div className="mt-3 text-xs text-muted">{t('lookingThrough')}</div>}
      {!uploading && build && build.carried > 0 && <div className="mt-2 text-xs text-faint">{t('carriedIn', build.carried)}</div>}
      {!uploading && build && build.known > 0 && <div className="mt-2 text-xs text-faint">{t('alreadyIn', build.known)}</div>}
      {!uploading && build && step.at < 3 && <CancelBuild id={build.id} compact={compact} />}
    </div>
  );
}
