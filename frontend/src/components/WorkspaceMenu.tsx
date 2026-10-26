// The workspace open, named in the top bar -- graphs kept apart, as separate environments --
// and the menu that opens another, makes one, or renames the one open. Opening another starts
// its graph and lets the old one go; everything drawn from the graph is asked again.
import { useQueryClient } from '@tanstack/react-query';
import { ChevronDown, Layers, Pencil, Plus } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { createPortal } from 'react-dom';
import { toast } from 'sonner';
import { api, call, ApiError } from '../api/client';
import { useT } from '../lib/i18n';
import { keys, useWorkspaces } from '../lib/queries';
import { Button, Spinner, Dialog, DialogContent, Input, Menu, MenuContent, MenuItem, MenuLabel, MenuRadio, MenuRadioGroup, MenuSeparator, MenuTrigger } from './ui';

export function WorkspaceMenu() {
  const t = useT();
  const client = useQueryClient();
  const { data } = useWorkspaces();
  const [naming, setNaming] = useState<{ id?: string; name: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [opening, setOpening] = useState<string | null>(null);
  if (!data?.available || !data.active || !data.items) return null;
  const { active, items } = data;
  const current = items.find((i) => i.id === active);

  const failed = (error: unknown) => toast.error(t('workspaceFailed'),
    { description: error instanceof ApiError && error.status === 409 ? t('workspaceBusy') : (error as Error).message });
  const open = async (id: string, name = items.find((i) => i.id === id)?.name ?? '') => {
    setBusy(true);
    setOpening(name);
    try {
      const listed = await call(api.POST('/v1/workspaces/{workspace}/open', { params: { path: { workspace: id } } }));
      client.setQueryData(keys.workspaces, listed);
      // Another graph: every answer drawn from the last one is gone, the list of workspaces aside.
      await client.resetQueries({ predicate: (q) => q.queryKey[0] !== keys.workspaces[0] });
      toast.success(t('workspaceOpened', name));
    } catch (error) {
      failed(error);
    } finally {
      setBusy(false);
      setOpening(null);
    }
  };
  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!naming) return;
    setBusy(true);
    try {
      if (naming.id) {
        await call(api.PATCH('/v1/workspaces/{workspace}', { params: { path: { workspace: naming.id } }, body: { name: naming.name } }));
        await client.invalidateQueries({ queryKey: keys.workspaces });
        setNaming(null);
      } else {
        const made = await call(api.POST('/v1/workspaces', { body: { name: naming.name } }));
        setNaming(null);
        setBusy(false);
        await open(made.id, made.name);
      }
    } catch (error) {
      failed(error);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {opening !== null && createPortal(
        // Another graph is being opened: what is on screen is the last one's, so it is covered.
        // Put on the body: the top bar's blur would hold a fixed box inside the bar.
        <div role="status" className="fixed inset-0 z-50 grid place-items-center bg-bg/80 backdrop-blur-sm">
          <div className="flex items-center gap-3 rounded-xl border border-line bg-panel px-5 py-4 text-sm shadow-pop">
            <Spinner className="size-4" />{t('workspaceOpening', opening)}
          </div>
        </div>, document.body)}
      <Menu>
        <MenuTrigger asChild>
          <Button variant="ghost" size="sm" busy={busy} aria-label={`${t('workspace')}: ${current?.name ?? ''}`} title={t('workspaceTip')}
            className="max-w-48 border border-line">
            <Layers className="size-3.5 shrink-0" /><span className="truncate">{current?.name}</span><ChevronDown className="size-3 shrink-0 text-faint" />
          </Button>
        </MenuTrigger>
        <MenuContent align="start">
          <MenuLabel>{t('workspace')}</MenuLabel>
          <MenuRadioGroup value={active} onValueChange={(id) => void open(id)}>
            {items.map((i) => <MenuRadio key={i.id} value={i.id}>{i.name}</MenuRadio>)}
          </MenuRadioGroup>
          <MenuSeparator />
          <MenuItem onSelect={() => setNaming({ name: '' })}><Plus className="mr-2 size-3.5" />{t('workspaceNew')}</MenuItem>
          <MenuItem onSelect={() => setNaming({ id: active, name: current?.name ?? '' })}><Pencil className="mr-2 size-3.5" />{t('workspaceRename')}</MenuItem>
        </MenuContent>
      </Menu>
      <Dialog open={!!naming} onOpenChange={(on) => { if (!on) setNaming(null); }}>
        <DialogContent title={naming?.id ? t('workspaceRename') : t('workspaceNew')}
          description={naming?.id ? undefined : t('workspaceNewBody')} closeLabel={t('close')}>
          <form onSubmit={(e) => void save(e)} className="grid gap-4">
            <Input autoFocus aria-label={t('workspaceName')} value={naming?.name ?? ''} maxLength={60}
              onChange={(e) => setNaming((n) => n && { ...n, name: e.target.value })} />
            <div className="flex justify-end gap-2">
              <Button type="button" onClick={() => setNaming(null)}>{t('cancel')}</Button>
              <Button type="submit" variant="primary" busy={busy} disabled={!naming?.name.trim()}>
                {naming?.id ? t('workspaceSave') : t('workspaceCreate')}
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}
