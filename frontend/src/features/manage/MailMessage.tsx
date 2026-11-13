// A mail message as a document: its headers, its body as the units it was cut into, and the
// documents it carried -- there are no pages to draw. Attachments are the links the map holds
// from the message to the documents it cites.
import { ArrowLeft, Mail } from 'lucide-react';
import { Link } from 'react-router-dom';
import type { DocumentGraph } from '../../api/client';
import { FileIcon } from '../../components/docs';
import { kindOf } from '../../graph/outline';
import { cn, shownName } from '../../lib/format';
import { useT } from '../../lib/i18n';
import { useCorpusMap } from '../../lib/queries';

const header = (graph: DocumentGraph) => graph.nodes.find((n) => kindOf(n) === 'Document')?.properties ?? {};
export const isMessage = (graph: DocumentGraph) => 'meta_mail_message_id' in header(graph);

const list = (v: unknown) => (Array.isArray(v) ? v.join(', ') : String(v ?? ''));

export function MailMessage({ id, name, graph, actions }: {
  id: string; name: string; graph: DocumentGraph; actions?: React.ReactNode;
}) {
  const t = useT();
  const p = header(graph);
  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-3 border-b border-line px-6 py-4">
        <Link to="/manage" className="text-faint hover:text-fg" aria-label={t('library')}><ArrowLeft className="size-4" /></Link>
        <Mail className="size-5 text-muted" />
        <h1 className="min-w-0 flex-1 truncate text-lg font-semibold" title={shownName(name)}>
          {list(p.meta_mail_subject) || shownName(name)}
        </h1>
        {actions}
      </div>
      <MailContent id={id} graph={graph} className="mx-auto w-full max-w-3xl overflow-y-auto p-6" />
    </div>
  );
}

/** The message itself -- headers, body, the documents it carried -- where a document would show
 * its pages: on its own page and in the search's preview. */
export function MailContent({ id, graph, className }: { id: string; graph: DocumentGraph; className?: string }) {
  const t = useT();
  const p = header(graph);
  const map = useCorpusMap().data;
  // Only the files the message carried (matched "embedded"); a document it merely names by number
  // is linked too, and is not an attachment.
  const attachments = (map?.links ?? []).filter((l) => l.from === id && l.matched_by.includes('embedded'))
    .map((l) => map?.documents.find((d) => d.id === l.to)).filter((d): d is NonNullable<typeof d> => !!d);
  // The body's units, in the order the message had them; a quoted reply's earlier text stays muted.
  const body = graph.nodes.filter((n) => n.properties.meta_body_index !== undefined && typeof n.properties.text === 'string')
    .sort((a, b) => Number(a.properties.meta_body_index) - Number(b.properties.meta_body_index));
  const rows: [string, string][] = [
    [t('mailFrom'), list(p.meta_mail_from)], [t('mailTo'), list(p.meta_mail_to)],
    [t('mailCc'), list(p.meta_mail_cc)], [t('mailDate'), list(p.meta_mail_date)],
  ];
  return (
    <div className={cn('space-y-4 text-sm', className)}>
      <dl data-testid="mail-headers" className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1">
        {rows.filter(([, v]) => v).map(([k, v]) => (
          <div key={k} className="contents"><dt className="text-faint">{k}</dt><dd className="break-all">{v}</dd></div>
        ))}
      </dl>
      <section aria-label={t('mailBodyTitle')} className="space-y-3 rounded-xl border border-line bg-panel p-4">
        {body.map((n) => (
          <p key={n.id} className={n.properties.meta_mail_quoted ? 'whitespace-pre-wrap text-muted' : 'whitespace-pre-wrap'}>
            {String(n.properties.text)}
          </p>
        ))}
      </section>
      <section>
        <h2 className="mb-1 font-semibold">{t('mailAttachments')}</h2>
        {attachments.length === 0 ? <p className="text-muted">{t('mailNoAttachments')}</p> : (
          <ul className="space-y-1">
            {attachments.map((d) => (
              <li key={d.id}>
                <Link to={`/manage/docs/${encodeURIComponent(d.id)}`} className="inline-flex items-center gap-2 text-accent-fg hover:underline">
                  <FileIcon file={d.label} className="size-4" />{shownName(d.label)}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
