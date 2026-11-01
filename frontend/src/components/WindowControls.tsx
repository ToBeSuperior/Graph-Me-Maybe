// Minimize, maximize and close, drawn by the page where the window has handed its title bar to
// the header (Windows: graphme/window_chrome.py). Anywhere else nothing is drawn.
import { Copy, Minus, Square, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useT } from '../lib/i18n';

interface ChromeApi {
  chrome(): Promise<boolean>;
  minimize(): Promise<void>;
  toggle_maximize(): Promise<boolean>;
  maximized(): Promise<boolean>;
  close(): Promise<void>;
}

const api = (): ChromeApi | undefined =>
  (window as unknown as { pywebview?: { api?: ChromeApi } }).pywebview?.api;

/** Whether this window's title bar is the header: asked once the window's bridge is ready. */
export function useWindowChrome(): boolean {
  const [on, setOn] = useState(false);
  useEffect(() => {
    const ask = () => { void api()?.chrome?.().then(setOn).catch(() => setOn(false)); };
    if (api()) ask();
    window.addEventListener('pywebviewready', ask);
    return () => window.removeEventListener('pywebviewready', ask);
  }, []);
  return on;
}

const button = 'app-no-drag grid h-12 w-[46px] place-items-center text-muted transition-colors hover:text-fg';

export function WindowControls() {
  const t = useT();
  const [zoomed, setZoomed] = useState(false);
  useEffect(() => {
    // A double click on the header or a snap maximizes too: asked again whenever the size changes.
    const ask = () => { void api()?.maximized().then(setZoomed).catch(() => {}); };
    ask();
    window.addEventListener('resize', ask);
    return () => window.removeEventListener('resize', ask);
  }, []);
  return (
    <div className="-mr-3 ml-1 flex h-12 shrink-0 sm:-mr-4">
      <button type="button" aria-label={t('windowMinimize')} title={t('windowMinimize')}
        className={`${button} hover:bg-hover`} onClick={() => void api()?.minimize()}>
        <Minus className="size-4" strokeWidth={1.5} />
      </button>
      <button type="button" aria-label={t(zoomed ? 'windowRestore' : 'windowMaximize')} title={t(zoomed ? 'windowRestore' : 'windowMaximize')}
        className={`${button} hover:bg-hover`} onClick={() => void api()?.toggle_maximize().then(setZoomed)}>
        {zoomed ? <Copy className="size-3.5 -scale-x-100" strokeWidth={1.5} /> : <Square className="size-3.5" strokeWidth={1.5} />}
      </button>
      <button type="button" aria-label={t('windowClose')} title={t('windowClose')}
        className={`${button} hover:bg-[#c42b1c] hover:text-white`} onClick={() => void api()?.close()}>
        <X className="size-4" strokeWidth={1.5} />
      </button>
    </div>
  );
}
