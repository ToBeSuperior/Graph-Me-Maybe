import { beforeEach, describe, expect, it } from 'vitest';
import type { CorpusMap } from '../api/client';
import { graphStamp, keep, kept } from './kept';

const store = new Map<string, string>();
beforeEach(() => {
  store.clear();
  globalThis.sessionStorage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => { store.set(k, v); } } as Storage;
});

const map = (hash: string, similar = 0.9): CorpusMap => ({
  total: 1, documents: [{ id: 'a', label: 'a.docx', content_hash: hash, mentions: 0 }],
  links: [], similar: [{ from: 'a', to: 'b', score: similar }],
});

describe('kept answers', () => {
  it('come back for the same graph, and not once it changed', () => {
    const stamp = graphStamp(map('h1'));
    keep('ask', 'q', 5, stamp, { answer: 'x' });
    expect(kept('ask', 'q', 5, graphStamp(map('h1')))).toEqual({ answer: 'x' });
    expect(kept('ask', 'q', 5, graphStamp(map('h2')))).toBeUndefined();
    expect(kept('ask', 'q', 5, graphStamp(map('h1', 0.8)))).toBeUndefined();
    expect(kept('ask', 'q', 10, stamp)).toBeUndefined();
    expect(kept('search', 'q', 5, stamp)).toBeUndefined();
  });
});
