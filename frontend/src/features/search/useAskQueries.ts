// A question's search and answer, asked as the results page needs them: the answer only once
// something is found; both kept for this tab under the graph they came from (lib/kept.ts), so a
// reload shows them again without asking; both stopped together.
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { api, call, type Answer, type Schemas } from '../../api/client';
import { keep, kept } from '../../lib/kept';
import { keys } from '../../lib/queries';

type Found = Schemas['Found'];

export function useAskQueries(question: string, topK: number, stamp: string | null, onFound: () => void) {
  const client = useQueryClient();
  const [answerOn, setAnswerOn] = useState(true);
  const [stopped, setStopped] = useState(false);

  const search = useQuery({
    queryKey: keys.search(question, topK, stamp),
    queryFn: ({ signal }) => call(api.POST('/v1/search', { body: { question, top_k: topK }, signal })),
    initialData: () => (stamp ? kept<Found>('search', question, topK, stamp) : undefined),
    enabled: !stopped,
    retry: false,
  });
  // The answer is written once something is found: nothing found, there is nothing to write from.
  const answer = useQuery({
    queryKey: keys.ask(question, topK, stamp),
    queryFn: ({ signal }) => call(api.POST('/v1/ask', { body: { question, top_k: topK }, signal })),
    initialData: () => (stamp ? kept<Answer>('ask', question, topK, stamp) : undefined),
    enabled: answerOn && !stopped && !!search.data?.sources.length,
    retry: false,
  });

  useEffect(() => { if (search.isSuccess) onFound(); }, [search.isSuccess, onFound]);
  useEffect(() => { if (stamp && search.data) keep('search', question, topK, stamp, search.data); }, [search.data, stamp, question, topK]);
  useEffect(() => { if (stamp && answer.data) keep('ask', question, topK, stamp, answer.data); }, [answer.data, stamp, question, topK]);

  const cancel = (which: 'search' | 'ask') => client.cancelQueries({ queryKey: keys[which](question, topK, stamp) });
  return {
    search, answer, stopped,
    answerState: (!answerOn ? 'off' : answer.isError ? 'error' : answer.isFetching || answer.isPending ? 'pending' : 'done') as
      'off' | 'pending' | 'done' | 'error',
    stopSearch: () => { setStopped(true); void cancel('search'); void cancel('ask'); },
    searchAgain: () => setStopped(false),
    stopAnswer: () => { void cancel('ask'); setAnswerOn(false); },
    writeAnswer: () => { setAnswerOn(true); void answer.refetch(); },
  };
}
