// Ungrouping a version asks first, in a dialog naming the file: it stands on its own row and as
// its own search result from then on, and is never paired with the others directly again.
import { useQueryClient } from '@tanstack/react-query';
import { Unlink } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { api, call, type VersionOf } from '../../api/client';
import { Button, Dialog, DialogContent, DialogTrigger } from '../../components/ui';
import { shownName } from '../../lib/format';
import { useT } from '../../lib/i18n';
import { keys } from '../../lib/queries';

export function UngroupVersion({ version }: { version: VersionOf }) {
  const t = useT();
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const ungroup = async () => {
    setBusy(true);
    try {
      await call(api.POST('/v1/documents/{document_id}/ungroup', { params: { path: { document_id: version.document_id } } }));
      toast(t('ungrouped', shownName(version.file)));
      client.invalidateQueries({ queryKey: keys.library });
      setOpen(false);
    } catch (error) {
      toast.error(t('ungroupFailed'), { description: (error as Error).message });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <button type="button" title={t('ungroupTip')}
          className="inline-flex shrink-0 items-center gap-0.5 rounded px-1 text-2xs text-muted hover:bg-hover hover:text-fg">
          <Unlink className="size-3" aria-hidden />{t('ungroup')}
        </button>
      </DialogTrigger>
      <DialogContent title={t('ungroupTitle')} description={t('ungroupBody')} closeLabel={t('close')}>
        <p className="rounded-lg border border-line bg-subtle px-3 py-2 text-sm font-medium break-all">{shownName(version.file)}</p>
        <div className="mt-5 flex justify-end gap-2">
          <Button onClick={() => setOpen(false)}>{t('cancel')}</Button>
          <Button variant="primary" busy={busy} onClick={ungroup}>{t('ungroup')}</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
