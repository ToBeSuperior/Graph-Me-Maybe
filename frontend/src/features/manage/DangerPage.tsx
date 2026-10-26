// Emptying the graph: everything in it, and what the demo read, gone -- a folder added afterwards
// is read afresh. Said in numbers first, and done only once the reader types the word, so it is
// never one stray click.
import { useQueryClient } from '@tanstack/react-query';
import { Layers, TriangleAlert } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { api, call, isBuilding } from '../../api/client';
import { Button, Input } from '../../components/ui';
import { useT } from '../../lib/i18n';
import { graphReset, keys, useBuild, useCorpusMap, useWorkspaces } from '../../lib/queries';
import { PageHeader } from './ManageLayout';

export function DangerPage() {
  const t = useT();
  const client = useQueryClient();
  const { data: map } = useCorpusMap();
  const { data: build } = useBuild();
  const [typed, setTyped] = useState('');
  const [busy, setBusy] = useState(false);
  const word = t('resetWord');
  const total = map?.total ?? 0;

  const reset = async () => {
    setBusy(true);
    try {
      const got = await call(api.POST('/v1/graph/reset', { body: { confirm: true } }));
      toast.success(t('resetDone', got.deleted));
      setTyped('');
      graphReset(client);
    } catch (error) {
      toast.error(t('resetFailed'), { description: (error as Error).message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <PageHeader title={t('dangerZone')} description={t('dangerBody')} />
      <div className="max-w-2xl p-6">
        <section className="rounded-xl border border-danger-line bg-panel">
          <div className="flex items-start gap-3 p-5">
            <div className="grid size-9 shrink-0 place-items-center rounded-lg bg-danger-soft text-danger"><TriangleAlert className="size-4" /></div>
            <div className="min-w-0 flex-1">
              <h2 className="font-semibold">{t('resetTitle')}</h2>
              <p className="mt-1 text-sm text-muted">{t('resetBody', total)}</p>
              <ul className="mt-2 list-disc pl-5 text-sm text-muted">
                <li>{t('resetGoes1')}</li><li>{t('resetGoes2')}</li><li>{t('resetKeeps')}</li>
              </ul>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2 border-t border-danger-line/60 bg-danger-soft/40 px-5 py-4">
            <label className="text-sm" htmlFor="resetWord">{t('resetType', word)}</label>
            <Input id="resetWord" value={typed} onChange={(e) => setTyped(e.target.value)} className="w-40" autoComplete="off" />
            <Button variant="danger" className="ml-auto" busy={busy}
              disabled={typed.trim() !== word || isBuilding(build) || !total} onClick={reset}>
              {t('resetGraph')}
            </Button>
          </div>
          {isBuilding(build) && <p className="px-5 pb-4 text-xs text-warn">{t('resetWhileBuilding')}</p>}
        </section>
        <DeleteWorkspace />
      </div>
    </div>
  );
}

/** A workspace other than the one open, deleted with its graph once its name is typed. */
function DeleteWorkspace() {
  const t = useT();
  const client = useQueryClient();
  const { data } = useWorkspaces();
  const [picked, setPicked] = useState('');
  const [typed, setTyped] = useState('');
  const [busy, setBusy] = useState(false);
  if (!data?.available || !data.items) return null;
  const others = data.items.filter((i) => i.id !== data.active);
  const target = others.find((i) => i.id === picked) ?? others[0];

  const remove = async () => {
    if (!target) return;
    setBusy(true);
    try {
      client.setQueryData(keys.workspaces,
        await call(api.DELETE('/v1/workspaces/{workspace}', { params: { path: { workspace: target.id } } })));
      toast.success(t('workspaceDeleted', target.name));
      setTyped('');
      setPicked('');
    } catch (error) {
      toast.error(t('workspaceFailed'), { description: (error as Error).message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section data-testid="delete-workspace" className="mt-6 rounded-xl border border-danger-line bg-panel">
      <div className="flex items-start gap-3 p-5">
        <div className="grid size-9 shrink-0 place-items-center rounded-lg bg-danger-soft text-danger"><Layers className="size-4" /></div>
        <div className="min-w-0 flex-1">
          <h2 className="font-semibold">{t('workspaceDeleteTitle')}</h2>
          <p className="mt-1 text-sm text-muted">{t('workspaceDeleteBody')}</p>
        </div>
      </div>
      {target ? (
        <div className="flex flex-wrap items-center gap-2 border-t border-danger-line/60 bg-danger-soft/40 px-5 py-4">
          <select value={target.id} onChange={(e) => { setPicked(e.target.value); setTyped(''); }}
            className="h-8 rounded-md border border-line bg-panel px-2 text-sm text-fg">
            {others.map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}
          </select>
          <label className="text-sm" htmlFor="workspaceWord">{t('workspaceDeleteType', target.name)}</label>
          <Input id="workspaceWord" value={typed} onChange={(e) => setTyped(e.target.value)} className="w-48" autoComplete="off" />
          <Button variant="danger" className="ml-auto" busy={busy} disabled={typed.trim() !== target.name} onClick={remove}>
            {t('workspaceDelete')}
          </Button>
        </div>
      ) : (
        <p className="border-t border-danger-line/60 px-5 py-4 text-sm text-muted">{t('workspaceNoOther')}</p>
      )}
    </section>
  );
}
