// The main screen: the home page, or -- a question in the address (`?q=&k=`) -- its results.
// Results are keyed by the graph's fingerprint, so a changed graph asks again.
import { useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Skeleton } from '../../components/ui';
import { graphStamp } from '../../lib/kept';
import { useCorpusMap } from '../../lib/queries';
import { Home } from './Home';
import { Results } from './Results';
import { topKOf } from './SearchBox';

export function AskPage() {
  const [params] = useSearchParams();
  const question = params.get('q')?.trim() ?? '';
  const topK = topKOf(params.get('k'));
  const map = useCorpusMap();
  const stamp = useMemo(() => (map.data ? graphStamp(map.data) : null), [map.data]);
  // An old question's address opened on an empty graph: what is needed first is documents.
  if (!question || map.data?.total === 0) return <Home />;
  // What this tab kept is matched to the graph as it is now: known before the question is asked.
  if (map.isPending) return <div className="mx-auto max-w-3xl space-y-3 px-6 py-6"><Skeleton className="h-10" /><Skeleton className="h-24" /></div>;
  return <Results key={`${question}|${topK}|${stamp}`} question={question} topK={topK} stamp={stamp} />;
}
