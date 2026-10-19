// The app's words in Korean and English. `useT()` gives `t(key, ...args)` in the chosen
// language; a key missing from either language, or called with the wrong arguments, does not
// compile -- English is typed as Korean's shape.
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { WORDS, type Words } from './words';

export type Lang = 'ko' | 'en';
export type WordKey = keyof Words;
type Args<K extends WordKey> = Words[K] extends (...a: infer A) => string ? A : [];
export type T = <K extends WordKey>(key: K, ...args: Args<K>) => string;

const read = (): Lang => {
  try {
    const saved = localStorage.getItem('lang');
    if (saved === 'ko' || saved === 'en') return saved;
  } catch { /* storage off */ }
  return 'en'; // English unless Korean is chosen, whatever the browser's language
};

const LangContext = createContext<{ lang: Lang; setLang: (l: Lang) => void }>({ lang: 'en', setLang: () => {} });

export function LangProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(read);
  useEffect(() => { document.documentElement.lang = lang; }, [lang]);
  const setLang = (next: Lang) => {
    try { localStorage.setItem('lang', next); } catch { /* storage off */ }
    setLangState(next);
  };
  return <LangContext.Provider value={{ lang, setLang }}>{children}</LangContext.Provider>;
}

export const useLang = () => useContext(LangContext);

/** One `t` per language: a graph drawn with it is drawn again when the language changes, not
 * on every render -- redrawn on a click, it lost the double-click that followed. */
export function useT(): T {
  const { lang } = useLang();
  return useMemo(() => ((key: WordKey, ...args: unknown[]) => {
    const word = WORDS[lang][key] as unknown;
    return typeof word === 'function' ? (word as (...a: unknown[]) => string)(...args) : (word as string);
  }) as T, [lang]);
}
