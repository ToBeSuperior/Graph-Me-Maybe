// Files dropped on a conversation become what it asks about. In the app's own window a dropped
// file carries its path (`pywebviewFullPath`): the paths are found in the graphs by content
// (`POST /v1/scope`), the ones no graph holds are built first into the workspace open, and those
// another workspace holds are offered to be asked there. In a browser a file has no path: it is
// uploaded and built as a folder is, and the documents it became are the scope. A mail workspace
// takes no file: the reader is offered to ask in another workspace, which is opened first.
import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useState } from 'react';
import { toast } from 'sonner';
import { api, call, errorText, lastBuild, type CorpusMap, type Schemas } from '../../api/client';
import { useT } from '../../lib/i18n';
import { corpusMapOptions, keys, useWorkspaces } from '../../lib/queries';
import { droppedFiles, useUpload } from '../upload/upload';

export type Elsewhere = Schemas['Elsewhere'];

const ENDED = ['done', 'cancelled', 'error'];
const BUILD_POLL_MS = 2000;

/** The documents the files uploaded became. An upload's id is the start of its content's hash,
 * so a file the graph held already is found too -- by what it holds, never by its name. */
export const uploadedDocuments = (documents: CorpusMap['documents'], files: string[]) =>
  documents.filter((d) => files.some((f) => d.content_hash?.startsWith(f))).map((d) => d.id);

/** The path the app's window gives a dropped file; none in a browser. */
export const pathOf = (file: File) => (file as File & { pywebviewFullPath?: string }).pywebviewFullPath || null;

export function useScopeDrop({ active, onScope }: {
  /** The workspace open: the only one a dropped file is built into. */
  active: string;
  /** The documents dropped, in the workspace holding them. */
  onScope: (documents: string[], workspace: string) => Promise<void>;
}) {
  const t = useT();
  const client = useQueryClient();
  const { add } = useUpload();
  const [waiting, setWaiting] = useState(false);
  const [elsewhere, setElsewhere] = useState<Elsewhere[]>([]);
  const { data: workspaces } = useWorkspaces();
  const inMail = workspaces?.items?.find((i) => i.id === active)?.kind === 'mail';
  const other = workspaces?.items?.find((i) => i.kind !== 'mail' && i.id !== active);

  /** Until build `id` ends, the app's build query kept up to date as it goes. */
  const builtOut = useCallback(async (id: string) => {
    for (;;) {
      await new Promise((ok) => setTimeout(ok, BUILD_POLL_MS));
      const build = lastBuild(await call(api.GET('/v1/jobs')));
      client.setQueryData(keys.build, build);
      if (!build || build.id !== id || ENDED.includes(build.state)) return;
    }
  }, [client]);

  const byPaths = useCallback(async (paths: string[], into = active) => {
    const scopeOf = () => call(api.POST('/v1/scope', { body: { paths } }));
    let scope = await scopeOf();
    if (scope.missing.length) {
      if (scope.build_into && scope.build_into === into) {
        const build = await call(api.POST('/v1/jobs/build', { body: { paths: scope.missing, embed: true } }));
        client.setQueryData(keys.build, build);
        await builtOut(build.id);
        scope = await scopeOf();
      } else {
        // ponytail: built only into the workspace open; opening the one holding the rest first
        // would build there, at the cost of switching the whole app under the reader.
        toast.warning(t('scopeNotBuilt', scope.missing.length));
      }
    }
    setElsewhere(scope.elsewhere ?? []);
    if (scope.document_ids.length) await onScope(scope.document_ids, scope.workspace ?? into);
    else if (!scope.elsewhere?.length) toast.error(t('scopeNothing'));
  }, [active, builtOut, client, onScope, t]);

  const byUpload = useCallback(async (files: Promise<{ file: File; path: string }[]>, workspace: string) => {
    const picked = await files;
    const got = await add(picked);
    if (!got) return;
    await builtOut(got.build.id);
    await client.invalidateQueries({ queryKey: keys.map });
    const ids = uploadedDocuments((await client.fetchQuery(corpusMapOptions)).documents, got.files);
    if (ids.length) await onScope(ids, workspace);
    else toast.error(t('scopeNothing'));
  }, [add, builtOut, client, onScope, t]);

  const run = useCallback(async (work: () => Promise<void>) => {
    setWaiting(true);
    try {
      await work();
    } catch (error) {
      toast.error(t('scopeFailed'), { description: errorText(error) });
    } finally {
      setWaiting(false);
    }
  }, [t]);

  const drop = useCallback((data: DataTransfer) => {
    // read now: a drop's data is emptied once its event is over
    const paths = [...data.files].map(pathOf).filter((p): p is string => !!p);
    const files = paths.length ? null : droppedFiles(data.items);
    const ask = (into: string) => (files ? byUpload(files, into) : byPaths(paths, into));
    if (!inMail) { void run(() => ask(active)); return; }
    // A mail workspace follows its mailbox alone: what was dropped is asked in another one --
    // files it already holds found there, the rest built into it once it is open.
    if (!other) { toast.error(t('scopeMailOnly')); return; }
    toast(t('scopeMailWorkspace'), { action: { label: t('askInWorkspace', other.name), onClick: () => void run(async () => {
      const listed = await call(api.POST('/v1/workspaces/{workspace}/open', { params: { path: { workspace: other.id } } }));
      client.setQueryData(keys.workspaces, listed);
      await ask(other.id);
    }) } });
  }, [active, byPaths, byUpload, client, inMail, other, run, t]);

  /** The files another workspace holds, asked there. */
  const askThere = useCallback((workspace: string) => {
    const paths = elsewhere.filter((e) => e.workspace === workspace).map((e) => e.path);
    setElsewhere([]);
    void run(() => byPaths(paths));
  }, [byPaths, elsewhere, run]);

  /** Paths handed over another way than a drop (the small window's picked files), asked as
   * dropped paths are: built first into the workspace open when it may take them. */
  const askPaths = useCallback((paths: string[]) => run(() => byPaths(paths)), [byPaths, run]);

  return { drop, waiting, elsewhere, askThere, askPaths };
}
