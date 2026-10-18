// The home page: before anything is in the graph, a place to drop a folder (or try the samples);
// after, one question box with the questions asked before and, with the samples in, some to try.
import { Database, History } from 'lucide-react';
import { Link } from 'react-router-dom';
import { Button, Empty, Skeleton } from '../../components/ui';
import { useT } from '../../lib/i18n';
import { useCorpusMap } from '../../lib/queries';
import { BuildProgress, useIncoming } from '../upload/BuildProgress';
import { FolderDrop } from '../upload/FolderDrop';
import { RecentList, useHistory } from './RecentList';
import { SearchBox, useAsk } from './SearchBox';

/** A file the samples are written as (src/graphme/samples.py): with it in the graph, the
 * home page offers questions the samples answer. */
const SAMPLE_FILE = 'QP-210_기밀 시험 절차서.docx';

export function Home() {
  const t = useT();
  const ask = useAsk();
  const map = useCorpusMap();
  const incoming = useIncoming();
  const { history, forget } = useHistory();
  const total = map.data?.total ?? 0;
  const hasSamples = !!map.data?.documents.some((d) => d.label === SAMPLE_FILE);

  if (map.isPending) {
    return <div className="mx-auto max-w-2xl space-y-4 px-6 pt-24"><Skeleton className="h-10 w-2/3" /><Skeleton className="h-14" /></div>;
  }
  if (map.isError) {
    return (
      <Empty className="pt-24" icon={<Database className="size-5" />} title={t('dbUnreachable')}>
        <p>{(map.error as Error).message}</p>
        <Button className="mt-3" onClick={() => map.refetch()}>{t('retry')}</Button>
      </Empty>
    );
  }

  if (!total) {
    return (
      <div className="mx-auto flex max-w-3xl flex-col gap-6 px-4 pt-14 pb-10 sm:px-6 sm:pt-20">
        <div className="text-center">
          <h1 className="text-2xl font-semibold tracking-tight sm:text-[32px] sm:leading-tight">{t('heroTitle')}</h1>
          <p className="mx-auto mt-3 max-w-xl text-base text-muted">{t('heroBody')}</p>
        </div>
        {incoming ? <BuildProgress /> : <FolderDrop large />}
        <ol className="grid gap-3 sm:grid-cols-3">
          {(['howRead', 'howLink', 'howAsk'] as const).map((key, i) => (
            <li key={key} className="rounded-xl border border-line bg-panel p-4">
              <div className="mb-2 grid size-6 place-items-center rounded-full bg-accent-soft font-mono text-xs font-semibold text-accent-fg">{i + 1}</div>
              <div className="text-sm font-medium">{t(`${key}Title`)}</div>
              <div className="mt-1 text-xs leading-5 text-muted">{t(`${key}Body`)}</div>
            </li>
          ))}
        </ol>
      </div>
    );
  }

  const examples = hasSamples ? [t('example1'), t('example2'), t('example3')] : [];
  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-8 px-4 pt-16 pb-10 sm:px-6 sm:pt-24">
      <div className="text-center">
        <h1 className="text-2xl font-semibold tracking-tight sm:text-[28px]">{t('homeTitle')}</h1>
        <p className="mt-2 text-sm text-muted">{t('homeBody', total)}</p>
      </div>
      <SearchBox large autoFocus />
      {incoming && <BuildProgress />}
      {examples.length > 0 && (
        <div>
          <h2 className="mb-2 text-xs font-medium text-faint">{t('tryAsking')}</h2>
          <div className="flex flex-wrap gap-2">
            {examples.map((q) => (
              <button key={q} onClick={() => ask(q)}
                className="rounded-full border border-line bg-panel px-3 py-1.5 text-sm text-muted hover:border-accent-line hover:text-fg">{q}</button>
            ))}
          </div>
        </div>
      )}
      {history.length > 0 && (
        <div>
          <h2 className="mb-1 flex items-center gap-1.5 text-xs font-medium text-faint"><History className="size-3.5" />{t('recent')}</h2>
          <RecentList roomy history={history} onAsk={ask} onForget={forget} />
        </div>
      )}
      <p className="text-center text-xs text-faint">
        {t('homeManage')} <Link to="/manage" className="text-accent-fg hover:underline">{t('navManage')} →</Link>
      </p>
    </div>
  );
}
