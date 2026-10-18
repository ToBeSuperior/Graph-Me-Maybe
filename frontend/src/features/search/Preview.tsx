// The chosen document on the right: its rendered pages, drawn the first time it is asked for,
// and the passages that brought it here -- one at a time, each taking the viewer to the page it
// is on. Pages holding a passage are marked, so where to look is plain before reading.
import { ChevronLeft, ChevronRight, ExternalLink } from 'lucide-react';
import { memo, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { pageImage } from '../../api/client';
import { FileIcon, HopBadge, RelationChip } from '../../components/docs';
import { PageViewer, type Mark } from '../../components/PageViewer';
import { PagesPending, PageTabs } from '../../components/PageTabs';
import { Button } from '../../components/ui';
import { changedAt, cn, formatOf, shownName, stem } from '../../lib/format';
import { useLang, useT } from '../../lib/i18n';
import { pageName, pageNameAt, usePages } from '../../lib/pages';
import { snippet, type Row } from './ResultTree';

/** Kept as it is while the results around it change -- a citation hovered, a folder opened. Keyed
 * by the document it shows, so another document starts afresh. */
export const Preview = memo(function Preview({ row, question }: { row: Row; question: string }) {
  const t = useT();
  const { lang } = useLang();
  const { doc } = row.entry;
  const { doc: demoDoc, sheets, pagesOfNodes, boxes, error } = usePages(doc.id);
  const [passage, setPassage] = useState(0);
  const [page, setPage] = useState<number | null>(null);
  const format = formatOf(doc.label);

  // Where each passage is: the page its first drawn unit is on.
  const passagePages = useMemo(() => doc.passages.map((p) =>
    p.members.map((m) => boxes[m]?.page ?? pagesOfNodes[m]).find((x) => x !== undefined) ?? null), [doc, pagesOfNodes, boxes]);
  const marked = new Set(passagePages.filter((x): x is number => x !== null));

  // Opened on the page of the first passage, once where it is is known.
  useEffect(() => {
    if (page === null && passagePages[0] != null) setPage(passagePages[0]);
  }, [passagePages, page]);

  const pages = sheets?.sheets ?? [];
  const shown = pages.find((s) => s.index === page) ?? pages[0];
  // On the page shown, the units of the passage being read boxed, and those of the others faintly.
  const marks: Mark[] = shown ? doc.passages.flatMap((p, i) => p.members
    .map((m) => boxes[m])
    .filter((b) => b && b.page === shown.index)
    .map((b): Mark => ({ box: b!.box as Mark["box"], strong: i === passage }))) : [];
  const goPassage = (i: number) => {
    setPassage(i);
    if (passagePages[i] != null) setPage(passagePages[i]);
  };
  const trail: Row[] = [];
  for (let r: Row | undefined = row; r; r = r.parent) trail.unshift(r);

  return (
    <div className="flex h-full flex-col">
      <div className="space-y-2 border-b border-line px-4 py-3">
        <nav className="flex min-w-0 flex-wrap items-center gap-1 text-xs text-faint" aria-label={t('breadcrumb')}>
          <span className="max-w-40 truncate" title={question}>“{question}”</span>
          {trail.map((r) => (
            <span key={r.key} className="flex min-w-0 items-center gap-1">
              <ChevronRight className="size-3 shrink-0" />
              {r.entry.direction && r.parent && <RelationChip direction={r.entry.direction} of={r.parent.entry.doc.label} />}
              <span className={cn('max-w-40 truncate', r === row && 'font-medium text-fg')}>{stem(r.entry.doc.label)}</span>
            </span>
          ))}
        </nav>
        <div className="flex items-start gap-2">
          <FileIcon file={doc.label} className="mt-1 size-5" />
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-base font-semibold" title={shownName(doc.label)}>{stem(doc.label)}</h2>
            <div className="flex flex-wrap items-center gap-x-2 text-xs text-muted">
              <HopBadge hop={doc.hop} />
              <span className="font-mono uppercase">{format}</span>
              {doc.modified != null && <span>· {t('modifiedAt', changedAt(doc.modified, lang, true))}</span>}
            </div>
          </div>
          <OpenInManage id={doc.id} />
        </div>
      </div>

      <section className="border-b border-line px-4 py-3">
        {doc.passages.length ? (
          <>
            <div className="mb-1.5 flex items-center gap-2">
              <h3 className="text-xs font-semibold text-muted">{t('matchedPassages')}</h3>
              <span className="font-mono text-2xs text-faint">{passage + 1}/{doc.passages.length}</span>
              <div className="ml-auto flex gap-0.5">
                <Button variant="ghost" size="icon-sm" aria-label={t('previous')} disabled={passage === 0} onClick={() => goPassage(passage - 1)}><ChevronLeft className="size-3.5" /></Button>
                <Button variant="ghost" size="icon-sm" aria-label={t('next')} disabled={passage >= doc.passages.length - 1} onClick={() => goPassage(passage + 1)}><ChevronRight className="size-3.5" /></Button>
              </div>
            </div>
            <blockquote className="max-h-32 overflow-y-auto rounded-lg border-l-2 border-accent bg-subtle px-3 py-2 text-sm leading-6">
              {snippet(doc.passages[passage].text, doc.label)}
            </blockquote>
            <div className="mt-1 flex items-center gap-2 text-2xs text-faint">
              <span title={t('relevanceTip')}>{t('relevance')} <span className="font-mono">{doc.passages[passage].score.toFixed(2)}</span></span>
              {passagePages[passage] != null && pages.length > 0 && (
                <button className="text-accent-fg hover:underline" onClick={() => setPage(passagePages[passage])}>
                  {t('onPage', pageNameAt(pages, passagePages[passage]!, format, t))}
                </button>
              )}
            </div>
          </>
        ) : (
          <p className="text-xs text-muted">{t('noPassagesLinkedOnly')}</p>
        )}
      </section>

      <div className="flex min-h-0 flex-1 flex-col gap-2 p-3">
        {error || !demoDoc || !sheets || !pages.length ? (
          <PagesPending error={error as Error | null} sheets={sheets} />
        ) : (
          <>
            <PageTabs pages={pages} current={shown.index} format={format} onPick={setPage} marked={marked} />
            <PageViewer className="min-h-0 flex-1" src={pageImage(demoDoc, shown.index)} alt={pageName(shown, format, t)}
              width={shown.width} height={shown.height} marks={marks} />
          </>
        )}
      </div>
    </div>
  );
});

/** "Open in Manage" as a link that looks like a button. */
export function OpenInManage({ id }: { id: string }) {
  const t = useT();
  return (
    <Link to={`/manage/docs/${encodeURIComponent(id)}`}
      className="inline-flex h-7 shrink-0 items-center gap-1.5 rounded-md border border-line px-2.5 text-xs font-medium text-muted hover:bg-hover hover:text-fg">
      <ExternalLink className="size-3.5" />{t('openInManage')}
    </Link>
  );
}
