// The questions of this tab, wherever they are listed: under the home page's box, in the results
// page's side column, or -- with no room for a column -- in a menu.
import { Clock, History, Plus, X } from 'lucide-react';
import { useCallback, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, Menu, MenuContent, MenuItem, MenuLabel, MenuTrigger } from '../../components/ui';
import { cn } from '../../lib/format';
import { useT } from '../../lib/i18n';
import { asked, forget, remember, type Asked } from './history';

export function useHistory() {
  const [history, setHistory] = useState<Asked[]>(asked);
  return {
    history,
    remember: useCallback((entry: Asked) => setHistory(remember(entry)), []),
    forget: useCallback((question: string) => setHistory(forget(question)), []),
  };
}

export function RecentList({ history, current, onAsk, onForget, roomy = false }: {
  history: Asked[]; current?: Asked; onAsk: (q: string, k: number) => void; onForget: (q: string) => void; roomy?: boolean;
}) {
  const t = useT();
  return (
    <ul className={roomy ? '' : 'min-h-0 flex-1 overflow-y-auto px-2 pb-3'}>
      {history.map((h) => {
        const on = !!current && h.question === current.question && h.topK === current.topK;
        return (
          <li key={h.question} className={cn('group flex items-center gap-2 rounded-md', roomy && 'rounded-lg px-2', on ? 'bg-active' : 'hover:bg-hover')}>
            {roomy && <Clock className="size-3.5 shrink-0 text-faint" />}
            <button title={h.question} onClick={() => onAsk(h.question, h.topK)}
              className={cn('min-w-0 flex-1 truncate text-left text-sm', roomy ? 'py-2' : 'px-2 py-1.5', !roomy && (on ? 'font-medium' : 'text-muted'))}>
              {h.question}
            </button>
            {!on && (
              <button aria-label={t('forget')} onClick={() => onForget(h.question)}
                className="invisible mr-1 rounded p-0.5 text-faint group-hover:visible focus-visible:visible hover:text-fg">
                <X className="size-3" />
              </button>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** The recent questions where there is no side column for them. */
export function RecentMenu({ history, onAsk }: { history: Asked[]; onAsk: (q: string, k: number) => void }) {
  const t = useT();
  const navigate = useNavigate();
  return (
    <Menu>
      <MenuTrigger asChild>
        <Button variant="secondary" size="icon" aria-label={t('recent')}><History className="size-4" /></Button>
      </MenuTrigger>
      <MenuContent>
        <MenuLabel>{t('recent')}</MenuLabel>
        <MenuItem onSelect={() => navigate('/')}><Plus className="mr-2 size-3.5" />{t('newQuestion')}</MenuItem>
        {history.map((h) => <MenuItem key={h.question} onSelect={() => onAsk(h.question, h.topK)}>{h.question}</MenuItem>)}
      </MenuContent>
    </Menu>
  );
}
