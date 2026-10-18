// Light, dark, or the system's ('auto'): set on <html> as data-theme, which every colour token
// follows. What is painted on a canvas (the graphs) cannot follow a token by itself: it reads the
// colours when it draws, and `useThemeTick` says when to draw again.
import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';

export type Theme = 'auto' | 'light' | 'dark';

const read = (): Theme => {
  try { const t = localStorage.getItem('theme'); return t === 'light' || t === 'dark' ? t : 'auto'; } catch { return 'auto'; }
};

const ThemeContext = createContext<{ theme: Theme; setTheme: (t: Theme) => void; tick: number }>(
  { theme: 'auto', setTheme: () => {}, tick: 0 });

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(read);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (theme === 'auto') delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = theme;
    setTick((n) => n + 1);
  }, [theme]);
  useEffect(() => {
    const system = matchMedia('(prefers-color-scheme: dark)');
    const changed = () => { if (theme === 'auto') setTick((n) => n + 1); };
    system.addEventListener('change', changed);
    return () => system.removeEventListener('change', changed);
  }, [theme]);
  const setTheme = (next: Theme) => {
    try { localStorage.setItem('theme', next); } catch { /* storage off */ }
    setThemeState(next);
  };
  return <ThemeContext.Provider value={{ theme, setTheme, tick }}>{children}</ThemeContext.Provider>;
}

export const useTheme = () => useContext(ThemeContext);
/** Changes whenever the colours do: a canvas drawn with `token()` is drawn again on it. */
export const useThemeTick = () => useContext(ThemeContext).tick;

/** A colour token's value now, for what is painted rather than styled. */
export const token = (name: string) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim();
