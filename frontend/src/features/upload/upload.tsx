// Documents come in a folder at a time -- picked, dropped, or the samples -- and are uploaded
// one by one, then built into the graph. One upload for the whole app (`UploadProvider`), so the
// top bar, the home page and the manage pages all show the same one.
import { useQueryClient } from '@tanstack/react-query';
import { createContext, useContext, useState, type ReactNode } from 'react';
import { toast } from 'sonner';
import { api, call, sent, type Samples } from '../../api/client';
import { useT } from '../../lib/i18n';
import { keys } from '../../lib/queries';

const READABLE = /\.(xlsx|pptx|docx|pdf)$/i;

/** What a folder holds that is the user's and can be read: Office's lock files and anything
 * hidden are passed over. `path` is where the file sat in the folder. */
const readable = (file: File, path = file.webkitRelativePath || file.name) =>
  READABLE.test(file.name) && !file.name.startsWith('~$') && !path.split('/').some((p) => p.startsWith('.'));

/** Every file under what was dropped, folders walked. */
export async function droppedFiles(items: DataTransferItemList): Promise<{ file: File; path: string }[]> {
  const out: { file: File; path: string }[] = [];
  const walk = async (entry: FileSystemEntry, path: string): Promise<void> => {
    if (entry.isFile) {
      const file = await new Promise<File>((ok, fail) => (entry as FileSystemFileEntry).file(ok, fail));
      out.push({ file, path: `${path}${entry.name}` });
    } else if (entry.isDirectory) {
      const reader = (entry as FileSystemDirectoryEntry).createReader();
      // A directory reader gives its entries a batch at a time, until an empty batch.
      for (;;) {
        const batch = await new Promise<FileSystemEntry[]>((ok, fail) => reader.readEntries(ok, fail));
        if (!batch.length) break;
        for (const child of batch) await walk(child, `${path}${entry.name}/`);
      }
    }
  };
  const entries = [...items].map((item) => item.webkitGetAsEntry()).filter((e): e is FileSystemEntry => !!e);
  for (const entry of entries) await walk(entry, '');
  return out;
}

interface Uploading { done: number; total: number; current: string }

interface UploadApi {
  uploading: Uploading | null;
  /** Upload the readable files among `files` and build them into the graph. */
  add: (files: { file: File; path: string }[]) => Promise<void>;
  samples: () => Promise<void>;
  loadingSamples: boolean;
}

const UploadContext = createContext<UploadApi | null>(null);

export function UploadProvider({ children }: { children: ReactNode }) {
  const t = useT();
  const client = useQueryClient();
  const [uploading, setUploading] = useState<Uploading | null>(null);
  const [loadingSamples, setLoadingSamples] = useState(false);

  const started = (build: Awaited<ReturnType<typeof startBuild>>) => {
    client.setQueryData(keys.build, build);
    client.invalidateQueries({ queryKey: keys.library });
  };
  const startBuild = (files: string[]) => call(api.POST('/v1/jobs/build', { body: { files, embed: true } }));

  const add: UploadApi['add'] = async (files) => {
    const picked = files.filter(({ file, path }) => readable(file, path));
    const skipped = files.length - picked.length;
    if (!picked.length) { toast.error(t('nothingReadable')); return; }
    const ids: string[] = [];
    const failed: string[] = [];
    const why = new Set<string>();
    for (const [i, { file }] of picked.entries()) {
      setUploading({ done: i, total: picked.length, current: file.name });
      const form = new FormData();
      form.append('file', file);
      // When the reader's file was last changed: kept with it, as their folder shows it.
      form.append('modified', String(file.lastModified));
      try {
        ids.push((await sent<{ id: string }>('/v1/files', { method: 'POST', body: form })).id);
      } catch (error) {
        failed.push(file.name);
        why.add((error as Error).message);
      }
    }
    setUploading(null);
    if (failed.length) toast.error(t('notUploaded', failed.length), { description: `${failed.join(', ')} — ${[...why].join(' · ')}` });
    toast(t('uploaded', ids.length, skipped), { description: skipped ? t('skippedWhy') : undefined });
    if (!ids.length) return;
    try {
      started(await startBuild(ids));
    } catch (error) {
      toast.error(t('cannotBuild'), { description: String((error as Error).message) });
    }
  };

  const samples = async () => {
    setLoadingSamples(true);
    try {
      const got = await sent<Samples>('/v1/samples', { method: 'POST' });
      started(got.build);
      toast.success(t('samplesStarted', got.files.length));
    } catch (error) {
      toast.error(t('cannotBuild'), { description: String((error as Error).message) });
    } finally {
      setLoadingSamples(false);
    }
  };

  return (
    <UploadContext.Provider value={{ uploading, add, samples, loadingSamples }}>{children}</UploadContext.Provider>
  );
}

export function useUpload() {
  const got = useContext(UploadContext);
  if (!got) throw new Error('useUpload outside UploadProvider');
  return got;
}
