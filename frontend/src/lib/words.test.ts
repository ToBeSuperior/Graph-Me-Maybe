// What the app says it reads names every format it reads, in both languages.
import { describe, expect, it } from 'vitest';
import { WORDS } from './words';

describe('the formats named', () => {
  it.each(['ko', 'en'] as const)('include EML in %s', (lang) => {
    expect(WORDS[lang].dropFormats).toContain('EML');
    expect(WORDS[lang].notReadable).toContain('EML');
  });
});
