// The main screen: the home page, or -- a question in the address (`?q=&k=`, the old links and
// what an MCP client opens) -- a conversation begun for it, which asks it as its first turn.
import { useEffect, useRef } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { toast } from 'sonner';
import { errorText } from '../../api/client';
import { Skeleton } from '../../components/ui';
import { useT } from '../../lib/i18n';
import { useCorpusMap } from '../../lib/queries';
import { fromAddress } from '../chat/ChatPage';
import { Home } from './Home';
import { topKOf } from './SearchBox';

function FromAddress({ question, topK }: { question: string; topK: number }) {
  const t = useT();
  const navigate = useNavigate();
  const made = useRef(false);
  useEffect(() => {
    if (made.current) return;
    made.current = true;
    fromAddress(question, topK).then(({ path, state }) => navigate(path, { replace: true, state }), (error) => {
      toast.error(t('cannotStartChat'), { description: errorText(error) });
      navigate('/', { replace: true });
    });
  }, [question, topK, navigate, t]);
  return <div className="mx-auto max-w-3xl space-y-3 px-6 py-6"><Skeleton className="h-10" /><Skeleton className="h-24" /></div>;
}

export function AskPage() {
  const [params] = useSearchParams();
  const question = params.get('q')?.trim() ?? '';
  const map = useCorpusMap();
  // An old question's address opened on an empty graph: what is needed first is documents.
  if (!question || map.data?.total === 0) return <Home />;
  return <FromAddress key={question} question={question} topK={topKOf(params.get('k'))} />;
}
