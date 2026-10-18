// A document's rendered pages, wherever they are shown -- the search preview and the document
// page: which of the server's files draws them, the pages (asked again until they are drawn),
// where each node is on them, and what a page is called.
import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import { api, call, type Pages } from '../api/client';
import type { T } from './i18n';
import { keys } from './queries';

export const pageName = (sheet: { index: number; name?: string | null }, format: string,
  t: T) => sheet.name || t(format === 'pptx' ? 'slide' : format === 'xlsx' ? 'sheet' : 'page', sheet.index);

/** What the page at `index` is called, whether or not it is among `pages` yet. */
export const pageNameAt = (pages: { index: number; name?: string | null }[], index: number, format: string, t: T) =>
  pageName(pages.find((p) => p.index === index) ?? { index }, format, t);

/** A document's pages, drawn by the server the first time they are asked for: which file
 * draws them, then its pages, asked again until they are there. */
export function usePages(documentId: string) {
  const where = useQuery({
    queryKey: keys.pagesOf(documentId),
    queryFn: () => call(api.POST('/v1/documents/{document_id}/file', { params: { path: { document_id: documentId } } })),
    retry: false,
  });
  const doc = where.data?.file_id;
  const sheets = useQuery({
    queryKey: keys.sheets(doc ?? ''),
    enabled: !!doc,
    queryFn: () => call(api.GET('/v1/files/{file_id}/pages', { params: { path: { file_id: doc! } } })),
    // Asked again until drawn; a document the server cannot draw is not asked for forever.
    refetchInterval: (q) => (q.state.error || ready(q.state.data) ? false : 1500),
    retry: false,
    staleTime: 0,
  });
  // The server's file ids last only as long as its start: one kept from before a restart is
  // gone, and the document is asked for again by its id in the graph -- which may well be the
  // same id, drawn anew -- then its pages. Each id is asked about once, and a few at most.
  const lost = (sheets.error as { status?: number } | null)?.status === 404;
  const asked = useRef(new Set<string>());
  const { refetch: findAgain } = where;
  const { refetch: drawAgain } = sheets;
  useEffect(() => {
    if (!lost || !doc || asked.current.has(doc) || asked.current.size >= 3) return;
    asked.current.add(doc);
    void findAgain().then((found) => { if (found.data?.file_id === doc) void drawAgain(); });
  }, [lost, doc, findAgain, drawAgain]);
  const graph = useQuery({
    queryKey: keys.demoGraph(doc ?? ''),
    enabled: !!doc && !!sheets.data?.sheets.length,
    queryFn: () => call(api.GET('/v1/files/{file_id}/graph', { params: { path: { file_id: doc! } } })),
  });
  const boxes = useQuery({
    queryKey: keys.boxes(doc ?? ''),
    enabled: !!doc && !!sheets.data?.sheets.length && !sheets.data.pending,
    queryFn: () => call(api.GET('/v1/files/{file_id}/boxes', { params: { path: { file_id: doc! } } })),
    retry: false,
  });
  return { doc, sheets: sheets.data, pagesOfNodes: graph.data?.pages ?? EMPTY, crops: graph.data?.crops ?? [],
    boxes: boxes.data?.boxes ?? NO_BOXES, error: where.error ?? sheets.error };
}
const EMPTY: Record<string, number> = {};
const NO_BOXES: Record<string, { page: number; box: [number, number, number, number] }> = {};
const ready = (s?: Pages) => !!s && (s.sheets.length > 0 || (s.state === 'error' || s.state === 'cancelled'));

