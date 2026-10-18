import { describe, expect, it } from 'vitest';
import { blocks, inlines } from './markdown';

describe('an answer written in Markdown', () => {
  it('reads headings, lists, tables, rules and paragraphs as blocks', () => {
    const text = [
      '## 판정',
      '시험 압력은 **1.1 MPa**입니다 [spec.docx].',
      '둘째 줄.',
      '',
      '- 첫째 [a.pdf]',
      '- 둘째',
      '  이어지는 줄',
      '1. 하나',
      '2) 둘',
      '---',
      '| 항목 | 결과 |',
      '|---|---|',
      '| 누설 | 적합 |',
    ].join('\n');
    expect(blocks(text)).toEqual([
      { kind: 'h', level: 2, text: '판정' },
      { kind: 'p', text: '시험 압력은 **1.1 MPa**입니다 [spec.docx].\n둘째 줄.' },
      { kind: 'ul', items: ['첫째 [a.pdf]', '둘째 이어지는 줄'] },
      { kind: 'ol', items: ['하나', '둘'] },
      { kind: 'hr' },
      { kind: 'table', head: ['항목', '결과'], rows: [['누설', '적합']] },
    ]);
  });

  it('marks bold, italics, code and citations, and leaves the rest as text', () => {
    expect(inlines('**굵게** 와 *기울임* 과 `code` [메모.docx] 2 * 3 * 4 file_name_x')).toEqual([
      { kind: 'strong', text: '굵게' },
      { kind: 'text', text: ' 와 ' },
      { kind: 'em', text: '기울임' },
      { kind: 'text', text: ' 과 ' },
      { kind: 'code', text: 'code' },
      { kind: 'text', text: ' ' },
      { kind: 'cite', text: '메모.docx' },
      { kind: 'text', text: ' 2 * 3 * 4 file_name_x' },
    ]);
  });

  it('never makes markup of its own: HTML in the text stays text', () => {
    expect(blocks('<img src=x onerror=alert(1)>')).toEqual([{ kind: 'p', text: '<img src=x onerror=alert(1)>' }]);
    expect(inlines('<b>x</b>')).toEqual([{ kind: 'text', text: '<b>x</b>' }]);
  });
});
