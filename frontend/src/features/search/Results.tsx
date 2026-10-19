// A question's results in three panes: the questions asked, the answer above the folders of what
// was found, and the chosen document's pages. On a narrow screen the questions are a menu and the
// pages a sheet.
import { AlertTriangle, ChevronsDownUp, ChevronsUpDown, FolderSearch, Plus } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import type { Source } from '../../api/client';
import { Button, Empty, Sheet, Skeleton, Spinner } from '../../components/ui';
import { cn, formatOf, shownName } from '../../lib/format';
import { answerPath, citedFiles, hopTrail, placesOf, type TrailDoc } from '../../lib/hops';
import { useT } from '../../lib/i18n';
import { useMediaQuery } from '../../lib/useMediaQuery';
import { AnswerCard } from './AnswerCard';
import { DownloadFiles } from './DownloadFiles';
import { Preview } from './Preview';
import { RecentList, RecentMenu, useHistory } from './RecentList';
import { firstRow, ResultTree, type Row } from './ResultTree';
import { SearchBox, useAsk } from './SearchBox';
import { useAskQueries } from './useAskQueries';

export function Results({ question, topK, stamp }: { question: string; topK: number; stamp: string | null }) {
  const t = useT();
  const ask = useAsk();
  const { history, remember, forget } = useHistory();
  const found = useCallback(() => remember({ question, topK }), [remember, question, topK]);
  const q = useAskQueries(question, topK, stamp, found);
  const { search, answer } = q;
  const [selected, setSelected] = useState<Row | null>(null);
  /** Picked by the reader -- then the answer arriving does not move the preview. */
  const picked = useRef(false);
  const [lit, setLit] = useState<string | null>(null);
  const [formats, setFormats] = useState(new Set<string>());
  const [expandAll, setExpandAll] = useState(0);
  const narrow = useMediaQuery('(max-width: 1023px)');

  const trail = useMemo(() => hopTrail((search.data?.sources ?? []) as Source[]), [search.data]);
  const places = useMemo(() => placesOf(trail), [trail]);
  const byLabel = useMemo(() => new Map(trail.nodes.map((n) => [n.label, n])), [trail]);
  const cited = useMemo(() => (answer.data ? answerPath(trail, answer.data.answer).used : new Set<string>()), [trail, answer.data]);
  const present = useMemo(() => [...new Set(trail.nodes.map((n) => formatOf(n.label)))].sort(), [trail]);
  const linked = trail.nodes.filter((n) => n.hop > 1).length;

  // On a wide screen the first document found is open on the right from the start -- and once the
  // answer is in, the document it cites first, unless the reader has picked one.
  useEffect(() => {
    if (!narrow && !selected && trail.rows.length) setSelected(firstRow(trail, trail.rows[0].doc.id));
  }, [trail, narrow, selected]);
  useEffect(() => {
    if (narrow || picked.current || !answer.data) return;
    const first = [...citedFiles(answer.data.answer)].map((file) => byLabel.get(file)).find(Boolean);
    if (first) setSelected(firstRow(trail, first.id));
  }, [answer.data, byLabel, trail, narrow]);

  const pick = useCallback((row: Row | null) => { picked.current = true; setSelected(row); }, []);
  const cite = useCallback((doc: TrailDoc) => pick(firstRow(trail, doc.id)), [pick, trail]);
  const hover = useCallback((doc: TrailDoc | null) => setLit(doc?.id ?? null), []);

  const center = (
    <div className="mx-auto flex max-w-3xl flex-col gap-4 px-4 py-4 sm:px-6">
      <div className="flex items-center gap-2">
        <div className="min-w-0 flex-1"><SearchBox initial={question} topK={topK} /></div>
        {narrow && <RecentMenu history={history} onAsk={ask} />}
      </div>
      {q.stopped && !search.data ? (
        <Empty icon={<FolderSearch className="size-5" />} title={t('searchStopped')}>
          <Button className="mt-3" onClick={q.searchAgain}>{t('searchAgain')}</Button>
        </Empty>
      ) : search.isPending ? (
        <div className="space-y-3" role="status">
          <div className="flex items-center gap-2 text-sm text-muted">
            <Spinner />{t('stageSearching')}
            <Button size="sm" variant="ghost" className="ml-auto" onClick={q.stopSearch}>{t('stop')}</Button>
          </div>
          {[0, 1, 2, 3, 4].map((i) => <div key={i} className="flex gap-2"><Skeleton className="size-5" /><div className="flex-1 space-y-1.5"><Skeleton className="w-1/2" /><Skeleton className="h-3 w-5/6" /></div></div>)}
        </div>
      ) : search.isError ? (
        <Empty icon={<AlertTriangle className="size-5" />} title={t('searchFailed')}>
          <p>{(search.error as Error).message}</p>
          <Button className="mt-3" onClick={() => search.refetch()}>{t('retry')}</Button>
        </Empty>
      ) : !trail.rows.length ? (
        <Empty icon={<FolderSearch className="size-5" />} title={t('nothingFound')}>
          <p>{t('nothingFoundBody')}</p>
          <ul className="mt-2 list-disc space-y-1 text-left">
            <li>{t('tipFewerWords')}</li>
            <li>{t('tipNumbers')}</li>
          </ul>
          <div className="mt-4 flex justify-center gap-2">
            {topK < 20 && <Button onClick={() => ask(question, 20)}>{t('searchWider')}</Button>}
            <Link to="/manage" className="inline-flex h-8 items-center rounded-md px-3 text-sm text-accent-fg hover:underline">{t('checkLibrary')}</Link>
          </div>
        </Empty>
      ) : (
        <>
          <AnswerCard state={q.answerState} answer={answer.data?.answer} error={(answer.error as Error | null)?.message}
            byLabel={byLabel} onCite={cite} onHover={hover} onRetry={q.writeAnswer} onCancel={q.stopAnswer} />
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-sm font-semibold">{t('foundTitle')}</h2>
            <span className="text-xs text-muted">{t('foundSummary', trail.rows.length, linked)}</span>
            <div className="ml-auto flex flex-wrap items-center gap-1">
              {present.length > 1 && present.map((f) => (
                <button key={f} aria-pressed={formats.has(f)}
                  onClick={() => setFormats((s) => { const n = new Set(s); if (n.has(f)) n.delete(f); else n.add(f); return n; })}
                  className={cn('h-6 rounded-md border px-2 font-mono text-2xs uppercase',
                    formats.has(f) ? 'border-accent bg-accent-soft text-accent-fg' : 'border-line text-muted hover:bg-hover')}>{f}</button>
              ))}
              <DownloadFiles docs={trail.nodes} question={question} />
              <Button variant="ghost" size="icon-sm" aria-label={t('expandAll')} title={t('expandAll')} onClick={() => setExpandAll((n) => Math.abs(n) + 1)}><ChevronsUpDown className="size-3.5" /></Button>
              <Button variant="ghost" size="icon-sm" aria-label={t('collapseAll')} title={t('collapseAll')} onClick={() => setExpandAll((n) => -(Math.abs(n) + 1))}><ChevronsDownUp className="size-3.5" /></Button>
            </div>
          </div>
          <div className="flex flex-wrap gap-x-3 gap-y-1 text-2xs text-faint">
            {([1, 2, 3] as const).map((h) => (
              <span key={h} className="inline-flex items-center gap-1">
                <span className={cn('grid size-3.5 place-items-center rounded-full font-mono text-[9px] text-white', ['', 'bg-hop-1', 'bg-hop-2', 'bg-hop-3'][h])}>{h}</span>{t(`hop${h}`)}
              </span>
            ))}
          </div>
          <ResultTree trail={trail} selected={selected?.key ?? null} onSelect={pick} cited={cited} lit={lit}
            places={places} formats={formats} expandAll={expandAll} />
        </>
      )}
    </div>
  );

  return (
    <div className="grid h-full lg:grid-cols-[220px_minmax(0,1fr)_minmax(380px,42%)]">
      <aside className="hidden min-h-0 flex-col border-r border-line bg-subtle/50 lg:flex" aria-label={t('recent')}>
        <div className="p-3">
          <Link to="/" className="flex h-8 items-center gap-2 rounded-md border border-line bg-panel px-2.5 text-sm font-medium hover:bg-hover">
            <Plus className="size-4" />{t('newQuestion')}
          </Link>
        </div>
        <h2 className="px-4 pb-1 text-2xs font-medium tracking-wide text-faint uppercase">{t('recent')}</h2>
        <RecentList history={history} current={{ question, topK }} onAsk={ask} onForget={forget} />
      </aside>
      <div className="min-h-0 overflow-y-auto">{center}</div>
      {!narrow ? (
        <aside className="hidden min-h-0 border-l border-line bg-panel lg:block" aria-label={t('preview')}>
          {selected ? <Preview key={selected.entry.doc.id} row={selected} question={question} /> : (
            <Empty className="h-full" icon={<FolderSearch className="size-5" />} title={t('previewEmpty')}>{t('previewEmptyBody')}</Empty>
          )}
        </aside>
      ) : (
        <Sheet open={!!selected} onOpenChange={(o) => { if (!o) pick(null); }} title={shownName(selected?.entry.doc.label ?? '')} closeLabel={t('close')}>
          {selected && <Preview key={selected.entry.doc.id} row={selected} question={question} />}
        </Sheet>
      )}
    </div>
  );
}
