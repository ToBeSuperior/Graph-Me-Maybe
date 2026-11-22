import { describe, expect, it } from 'vitest';
import type { CorpusMap } from '../api/client';
import { graphStamp } from './kept';

const map = (hash: string, similar = 0.9): CorpusMap => ({
  total: 1, documents: [{ id: 'a', label: 'a.docx', content_hash: hash, mentions: 0 }],
  links: [], similar: [{ from: 'a', to: 'b', score: similar }],
});

describe('the graph stamp', () => {
  it('is the same for the same graph, and differs once it changed', () => {
    expect(graphStamp(map('h1'))).toBe(graphStamp(map('h1')));
    expect(graphStamp(map('h2'))).not.toBe(graphStamp(map('h1')));
    expect(graphStamp(map('h1', 0.8))).not.toBe(graphStamp(map('h1')));
  });
});
