// What the page shows when the server asks for its key (GRAPHME_API_KEY): one field, the key kept
// as a cookie, then the page loaded again so every request -- and every picture -- carries it.
import { KeyRound } from 'lucide-react';
import { useState } from 'react';
import { useT } from '../lib/i18n';
import { keyCookie, keySent, useKeyWanted } from '../lib/key';
import { Button, Input } from './ui';

export function KeyGate() {
  const t = useT();
  const wanted = useKeyWanted();
  const [key, setKey] = useState('');
  const [unusable, setUnusable] = useState(false);
  if (!wanted) return null;
  return (
    <div role="dialog" aria-modal="true" aria-labelledby="key-title" className="fixed inset-0 z-50 grid place-items-center bg-bg p-4">
      <form className="w-full max-w-sm space-y-3 rounded-xl border border-line bg-panel p-5 shadow-lg" onSubmit={(e) => {
        e.preventDefault();
        const cookie = keyCookie(key.trim(), location.protocol === 'https:');
        if (!cookie) { setUnusable(true); return; }
        document.cookie = cookie;
        location.reload();
      }}>
        <h2 id="key-title" className="flex items-center gap-2 font-semibold"><KeyRound className="size-4" />{t('keyTitle')}</h2>
        <p className="text-sm text-muted">{t(keySent(document.cookie) ? 'keyWrong' : 'keyBody')}</p>
        <Input type="password" autoFocus autoComplete="off" aria-label={t('keyLabel')} value={key}
          onChange={(e) => { setKey(e.target.value); setUnusable(false); }} />
        {unusable && <p role="alert" className="text-sm text-danger-fg">{t('keyUnusable')}</p>}
        <Button type="submit" variant="primary" className="w-full" disabled={!key.trim()}>{t('keySubmit')}</Button>
      </form>
    </div>
  );
}
