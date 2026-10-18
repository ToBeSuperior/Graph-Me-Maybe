import { describe, expect, it } from 'vitest';
import { keyCookie, keySent } from './key';

describe('the key as a cookie', () => {
  it('is kept for this site alone, and marked secure over https', () => {
    expect(keyCookie('k-invented+/=', false)).toBe('graphme_key=k-invented+/=; Path=/; SameSite=Strict');
    expect(keyCookie('k-invented', true)).toBe('graphme_key=k-invented; Path=/; SameSite=Strict; Secure');
  });
  it('is refused when a cookie cannot carry it as it is', () => {
    for (const key of ['', 'two words', 'a;b', 'a,b', 'a"b', 'a\\b', '키']) expect(keyCookie(key, false)).toBeNull();
  });
  it('was sent when the browser holds it', () => {
    expect(keySent('lang=ko; graphme_key=k')).toBe(true);
    expect(keySent('lang=ko; not_graphme_key=k')).toBe(false);
  });
});
