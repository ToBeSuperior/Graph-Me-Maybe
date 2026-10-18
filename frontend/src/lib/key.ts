// The server's key (GRAPHME_API_KEY), when it asks for one: put in once and kept as the cookie
// every request carries -- an <img> or a download link cannot send a header. SameSite=Strict:
// a request another site's page makes never carries it.
import { useSyncExternalStore } from 'react';

const KEY_COOKIE = 'graphme_key';
let wanted = false;
const listeners = new Set<() => void>();

/** A request was turned away for want of the key (401): the page asks for it, once. */
export function keyWanted() {
  if (wanted) return;
  wanted = true;
  listeners.forEach((listener) => listener());
}

export const useKeyWanted = () => useSyncExternalStore((listener) => {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}, () => wanted);

/** The cookie that holds `key` -- for this browser session only -- or null when a cookie cannot
 * hold it as it is: RFC 6265 takes no space, quote, comma, semicolon or backslash, and the server
 * reads the value as sent. */
export function keyCookie(key: string, secure: boolean): string | null {
  if (!/^[\x21\x23-\x2B\x2D-\x3A\x3C-\x5B\x5D-\x7E]+$/.test(key)) return null;
  return `${KEY_COOKIE}=${key}; Path=/; SameSite=Strict${secure ? '; Secure' : ''}`;
}

/** Whether a key was sent already: then a 401 says it is the wrong one. */
export const keySent = (cookies: string) => cookies.split(/;\s*/).some((c) => c.startsWith(`${KEY_COOKIE}=`));
