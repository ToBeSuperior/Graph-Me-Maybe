// Where documents come in: a folder dropped anywhere on the box, or picked with the button, or
// the samples. The box itself is the invitation -- on an empty graph it is the whole page.
import { FolderOpen, FolderPlus, Sparkles, Upload } from 'lucide-react';
import { useRef, useState, type ReactNode } from 'react';
import { Button } from '../../components/ui';
import { cn } from '../../lib/format';
import { useT } from '../../lib/i18n';
import { useSettings } from '../../lib/queries';
import { droppedFiles, useUpload } from './upload';

/** The hidden folder picker and a way to open it. */
function useFolderPicker() {
  const { add } = useUpload();
  const input = useRef<HTMLInputElement>(null);
  const element = (
    <input
      ref={input}
      type="file"
      hidden
      multiple
      // @ts-expect-error -- a folder picker: every browser takes it, React's types do not
      webkitdirectory=""
      onChange={(e) => {
        const files = [...(e.target.files ?? [])].map((file) => ({ file, path: file.webkitRelativePath || file.name }));
        e.target.value = '';
        void add(files);
      }}
    />
  );
  return { open: () => input.current?.click(), element };
}

export function AddFolderButton({ variant = 'secondary', size = 'md', children }: {
  variant?: 'primary' | 'secondary'; size?: 'sm' | 'md' | 'lg'; children?: ReactNode;
}) {
  const t = useT();
  const { uploading } = useUpload();
  const picker = useFolderPicker();
  return (
    <>
      {picker.element}
      <Button variant={variant} size={size} onClick={picker.open} busy={!!uploading}>
        {!uploading && <FolderPlus className="size-4" />}
        {children ?? t('addFolder')}
      </Button>
    </>
  );
}

export function FolderDrop({ large = false, className }: { large?: boolean; className?: string }) {
  const t = useT();
  const { add, samples, loadingSamples, uploading } = useUpload();
  const { data: settings } = useSettings();
  const picker = useFolderPicker();
  const [over, setOver] = useState(false);
  return (
    <div
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={async (e) => {
        e.preventDefault();
        setOver(false);
        await add(await droppedFiles(e.dataTransfer.items));
      }}
      className={cn('flex flex-col items-center justify-center rounded-xl border-2 border-dashed text-center transition-colors',
        over ? 'border-accent bg-accent-soft' : 'border-line bg-subtle/60',
        large ? 'gap-4 px-8 py-14' : 'gap-3 px-6 py-8', className)}
    >
      {picker.element}
      <div className={cn('grid place-items-center rounded-2xl bg-panel text-accent shadow-sm ring-1 ring-line',
        large ? 'size-14' : 'size-11')}>
        {over ? <Upload className="size-6" /> : <FolderOpen className="size-6" />}
      </div>
      <div>
        <div className={cn('font-semibold', large ? 'text-xl' : 'text-base')}>{t('dropTitle')}</div>
        <div className="mt-1 text-sm text-muted">{t('dropFormats')}</div>
      </div>
      <div className="flex flex-wrap items-center justify-center gap-2">
        <Button variant="primary" size={large ? 'lg' : 'md'} onClick={picker.open} busy={!!uploading}>
          {!uploading && <FolderPlus className="size-4" />}
          {t('pickFolder')}
        </Button>
        {/* The samples are the demo's (`tools/hierarchy_demo`); `graphme serve` has none. */}
        {settings?.samples && (
          <Button variant="secondary" size={large ? 'lg' : 'md'} onClick={samples} busy={loadingSamples}>
            {!loadingSamples && <Sparkles className="size-4" />}
            {t('trySamples')}
          </Button>
        )}
      </div>
      {large && <p className="max-w-md text-xs text-faint">{t('dropPrivacy')}</p>}
    </div>
  );
}
