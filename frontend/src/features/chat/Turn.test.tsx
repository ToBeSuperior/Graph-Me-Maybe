import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { ClaimDetails, withClaims } from './ClaimMarks';
import { Composer, withDocument, withoutDocument } from './Composer';
import { Turn, type TurnShape } from './Turn';

const source = { id: 'a:0', score: 1, document_id: 'a', file: 'Invented spec.docx', members: [] as string[], context: 'The test pressure is 2 bar.' };
const answer = 'The test pressure is 2 bar. [Invented spec.docx] It is held for 9 minutes.';
const turn = {
  question: 'What is the test pressure?',
  found: { sources: [source], queries: [], text_only: false },
  answer: { answer, sources: [source], text_only: false, checked: true, claims: [
    { text: 'The test pressure is 2 bar.', supported: true, files: ['Invented spec.docx'] },
    { text: 'It is held for 9 minutes.', supported: false, correction: 'It is held for 4 minutes.', files: ['Invented spec.docx'] },
  ] },
} satisfies TurnShape;

describe('a turn', () => {
  it('marks a statement the sources bear out with ✓, and one they correct with ⚠ and the correction', () => {
    const html = renderToStaticMarkup(<Turn turn={turn} onOpenDocument={() => {}} onAskAgain={() => {}} />);
    expect(html).toContain('aria-label="The sources bear this out"');
    expect(html).toContain('aria-label="The sources say otherwise"');
    expect(html).toContain('It is held for 4 minutes.');
  });

  it('says when it was asked of another graph than the one there is now', () => {
    const html = (now: string) => renderToStaticMarkup(
      <Turn turn={{ ...turn, stamp: 's1' }} now={now} onOpenDocument={() => {}} onAskAgain={() => {}} />);
    expect(html('s2')).toContain('The graph has changed since this answer');
    expect(html('s1')).not.toContain('The graph has changed since this answer');
  });
});

describe('claims in the answer', () => {
  it('put each mark after the statement it checked', () => {
    expect(withClaims(answer, turn.answer.claims)).toEqual(
      ['The test pressure is 2 bar.', 0, ' [Invented spec.docx] It is held for 9 minutes.', 1]);
    expect(withClaims('nothing checked here', turn.answer.claims)).toEqual(['nothing checked here']);
  });
});

describe('the scope', () => {
  it('“ask only this” puts the document in the scope chips', () => {
    const scope = withDocument(null, 'a');
    expect(scope).toEqual(['a']);
    expect(withDocument(scope, 'a')).toEqual(['a']);
    const html = renderToStaticMarkup(<Composer scope={scope.map((id) => ({ id, label: 'Invented spec.docx' }))} onAsk={() => {}} onRemove={() => {}} />);
    expect(html).toContain('Invented spec.docx');
    expect(html).not.toContain('All documents');
  });

  it('a chip taken off leaves the rest, and every document once none is left', () => {
    expect(withoutDocument(['a', 'b'], 'a')).toEqual(['b']);
    expect(withoutDocument(['a'], 'a')).toBeNull();
  });
});

describe('a statement the sources do not bear out, opened', () => {
  const struck = (correction: string | null) => renderToStaticMarkup(<ClaimDetails open={0} onToggle={() => {}}
    claims={[{ text: 'It is held for 9 minutes.', supported: false, correction, files: [] }]} />).includes('line-through');

  it('is struck through beside what the sources say instead', () => expect(struck('It is held for 4 minutes.')).toBe(true));
  it('is not struck through when the sources say nothing of it', () => expect(struck(null)).toBe(false));
});

describe('an earlier turn while another is being asked', () => {
  const none = { question: 'Where is the invented valve?', found: { sources: [], queries: [], text_only: false } } satisfies TurnShape;

  it('cannot ask again, so the turn running is not stopped by it', () => {
    const html = (busy: boolean) => renderToStaticMarkup(
      <Turn turn={none} busy={busy} onOpenDocument={() => {}} onAskAgain={() => {}} />);
    expect(html(true)).toMatch(/<button[^>]*disabled[^>]*title="Wait for the answer being written, or stop it"[^>]*>.*Search wider/);
    expect(html(false)).not.toContain('disabled=""');
    const stale = renderToStaticMarkup(<Turn turn={{ ...turn, stamp: 's1' }} now="s2" busy onOpenDocument={() => {}} onAskAgain={() => {}} />);
    expect(stale).toMatch(/<button[^>]*disabled[^>]*>Ask again/);
  });
});
