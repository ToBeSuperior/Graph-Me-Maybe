// The file a document was read from, opened on this machine in the app that opens it (Excel,
// Word, a PDF viewer) -- what an MCP client asks for too. The server opens it, from this machine
// only; a file moved since it was read says so.
import { ExternalLink } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { api, call } from '../../api/client';
import { Button } from '../../components/ui';
import { useT } from '../../lib/i18n';

export function OpenOriginal({ id }: { id: string }) {
  const t = useT();
  const [busy, setBusy] = useState(false);
  const open = async () => {
    setBusy(true);
    try {
      await call(api.POST('/v1/documents/{document_id}/open', { params: { path: { document_id: id } } }));
    } catch (error) {
      toast.error(t('cannotOpenOriginal'), { description: (error as Error).message });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Button variant="secondary" size="sm" busy={busy} onClick={() => void open()} title={t('openOriginalTip')}>
      <ExternalLink className="size-3.5" />{t('openOriginal')}
    </Button>
  );
}
