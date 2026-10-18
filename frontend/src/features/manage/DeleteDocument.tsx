// Deleting a document from the graph asks first, in a dialog that names it and says what goes
// with it -- its passages and every link to it. It cannot be undone: the file has to be added
// again, and it is read again.
import { shownName } from '../../lib/format';
import { useQueryClient } from '@tanstack/react-query';
import { Trash2 } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { api, call } from '../../api/client';
import { Button, Dialog, DialogContent, DialogTrigger } from '../../components/ui';
import { useT } from '../../lib/i18n';
import { graphChanged } from '../../lib/queries';

export function DeleteDocument({ id, name, onDeleted, compact = false }: {
  id: string; name: string; onDeleted?: () => void; compact?: boolean;
}) {
  const t = useT();
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const remove = async () => {
    setBusy(true);
    try {
      await call(api.DELETE('/v1/documents/{document_id}', { params: { path: { document_id: id } } }));
      toast.success(t('deleted', shownName(name)));
      graphChanged(client);
      setOpen(false);
      onDeleted?.();
    } catch (error) {
      toast.error(t('deleteFailed'), { description: (error as Error).message });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        {compact
          ? <Button variant="ghost" size="icon-sm" aria-label={t('deleteDoc')} title={t('deleteDoc')}><Trash2 className="size-3.5" /></Button>
          : <Button variant="danger-soft" size="sm"><Trash2 className="size-3.5" />{t('deleteDoc')}</Button>}
      </DialogTrigger>
      <DialogContent title={t('deleteTitle')} description={t('deleteBody')} closeLabel={t('close')}>
        <p className="rounded-lg border border-line bg-subtle px-3 py-2 text-sm font-medium break-all">{shownName(name)}</p>
        <div className="mt-5 flex justify-end gap-2">
          <Button onClick={() => setOpen(false)}>{t('cancel')}</Button>
          <Button variant="danger" busy={busy} onClick={remove}>{t('deleteDoc')}</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
