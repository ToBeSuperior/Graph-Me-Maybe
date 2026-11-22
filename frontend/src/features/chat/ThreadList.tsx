// The conversations kept for the workspace open, wherever they are listed: the chat's side
// column, under the home page's box, or -- with no room for a column -- a menu. Each is opened
// at its address (`/c/<id>`) and deleted from here.
import { useQueryClient } from '@tanstack/react-query';
import { MessagesSquare, MessageSquare, Plus, X } from 'lucide-react';
import { Link, useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import { api, call, errorText } from '../../api/client';
import { Button, Menu, MenuContent, MenuItem, MenuLabel, MenuTrigger } from '../../components/ui';
import { cn } from '../../lib/format';
import { useT } from '../../lib/i18n';
import { keys, useThreads } from '../../lib/queries';

function useForget(current?: string) {
  const t = useT();
  const client = useQueryClient();
  const navigate = useNavigate();
  return async (id: string) => {
    try {
      await call(api.DELETE('/v1/threads/{thread_id}', { params: { path: { thread_id: id } } }));
      client.removeQueries({ queryKey: keys.thread(id) });
      await client.invalidateQueries({ queryKey: keys.threads });
      if (id === current) navigate('/');
    } catch (error) {
      toast.error(t('cannotForget'), { description: errorText(error) });
    }
  };
}

export function ThreadList({ current, roomy = false }: { current?: string; roomy?: boolean }) {
  const t = useT();
  const { data } = useThreads();
  const forget = useForget(current);
  return (
    <ul className={roomy ? '' : 'min-h-0 flex-1 overflow-y-auto px-2 pb-3'} aria-label={t('recent')}>
      {(data ?? []).map((thread) => {
        const on = thread.id === current;
        return (
          <li key={thread.id} className={cn('group flex items-center gap-2 rounded-md', roomy && 'rounded-lg px-2', on ? 'bg-active' : 'hover:bg-hover')}>
            {roomy && <MessageSquare className="size-3.5 shrink-0 text-faint" />}
            <Link to={`/c/${thread.id}`} title={thread.title} aria-current={on ? 'page' : undefined}
              className={cn('min-w-0 flex-1 truncate text-left text-sm', roomy ? 'py-2' : 'px-2 py-1.5', !roomy && (on ? 'font-medium' : 'text-muted'))}>
              {thread.title || t('untitled')}
            </Link>
            <button aria-label={t('forget')} onClick={() => void forget(thread.id)}
              className="mr-1 rounded p-0.5 text-faint opacity-0 group-hover:opacity-100 focus-visible:opacity-100 hover:text-fg">
              <X className="size-3" />
            </button>
          </li>
        );
      })}
    </ul>
  );
}

/** The conversations where there is no side column for them. */
export function ThreadMenu() {
  const t = useT();
  const navigate = useNavigate();
  const { data } = useThreads();
  return (
    <Menu>
      <MenuTrigger asChild>
        <Button variant="secondary" size="icon" aria-label={t('recent')}><MessagesSquare className="size-4" /></Button>
      </MenuTrigger>
      <MenuContent>
        <MenuLabel>{t('recent')}</MenuLabel>
        <MenuItem onSelect={() => navigate('/')}><Plus className="mr-2 size-3.5" />{t('newQuestion')}</MenuItem>
        {(data ?? []).map((thread) => <MenuItem key={thread.id} onSelect={() => navigate(`/c/${thread.id}`)}>{thread.title || t('untitled')}</MenuItem>)}
      </MenuContent>
    </Menu>
  );
}
