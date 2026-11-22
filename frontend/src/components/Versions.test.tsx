import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { Source } from '../api/client';
import { ResultTree } from '../features/search/ResultTree';
import { hopTrail } from '../lib/hops';

const versions = [
  { document_id: 'final', file: 'Invented quay rules final.docx', modified: 1767312000, latest: true },
  { document_id: 'copy', file: 'Invented quay rules (copy).docx', modified: 1767225600, latest: false },
];
const source = { id: 'draft:0', score: 0.9, document_id: 'draft', file: 'Invented quay rules.docx', members: [],
  context: 'The quay closes at dusk.', modified: 1767139200, versions } satisfies Source;

const row = (sources: Source[]) => renderToStaticMarkup(
  <ResultTree trail={hopTrail(sources)} selected={null} onSelect={() => {}} cited={new Set()} lit={null}
    places={new Map()} formats={new Set()} expandAll={0} />);

describe('a found document with versions folded under it', () => {
  it('says how many with a closed "+N versions" row and which one is the latest', () => {
    const html = row([source]);
    expect(html).toContain('+2 versions');
    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain('Latest: Invented quay rules final');
  });

  it('shows no versions row for a document with none', () => {
    expect(row([{ ...source, versions: [] }])).not.toContain('versions');
  });
});
