// Small words about things: a text cut to fit, a file's name without its type, when it was
// last changed, what its pages are called.
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';
import type { Lang } from './i18n';

export const clip = (s: unknown, n: number) => {
  const text = String(s ?? '').replace(/\s+/g, ' ').trim();
  return text.length > n ? `${text.slice(0, n - 1)}…` : text;
};

export const stem = (file: string) => shownName(file).replace(/\.[^.]+$/, '');
/** A file's name without the digest a file carried inside another is read under
 * ("메모.emb1a2b3c4d.docx" → "메모.docx"): it tells two files apart, not the reader anything. */
export const shownName = (file: unknown): string => String(file ?? '').replace(/\.emb[0-9a-f]{8}(?=\.[A-Za-z0-9]{1,8}(?![A-Za-z0-9])|$)/g, '');
export const formatOf = (file: string) => (file.match(/\.([a-z0-9]+)$/i)?.[1] ?? '').toLowerCase();

/** When a file was last changed (seconds since 1970), as the system's folder shows it: the date
 * alone in a row, the date and time where there is room. */
export function changedAt(seconds: number | null | undefined, lang: Lang, full = false) {
  if (seconds == null) return '';
  return new Intl.DateTimeFormat(lang === 'ko' ? 'ko-KR' : 'en-US',
    full ? { dateStyle: 'medium', timeStyle: 'short' } : { dateStyle: 'medium' }).format(new Date(seconds * 1000));
}

/** A passage as the model was given it ends with the documents linked to its own; only the
 * passage itself is shown. */
export const ownText = (text: string) => String(text ?? '').split('\nLinked document, hop')[0];

/** Class names joined, a later Tailwind class winning over an earlier one of its kind. */
export const cn = (...parts: ClassValue[]) => twMerge(clsx(parts));
