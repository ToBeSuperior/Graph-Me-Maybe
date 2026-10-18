// An answer's Markdown, as far as a model writes it: headings, lists, tables and paragraphs, and
// inside them bold, italics, code and the [file] it cites. Parsed into plain parts the page draws
// as elements of its own -- nothing in the text is ever taken as HTML.

type Inline = { kind: 'text' | 'strong' | 'em' | 'code' | 'cite'; text: string };
type Block =
  | { kind: 'p'; text: string }
  | { kind: 'h'; level: number; text: string }
  | { kind: 'ul' | 'ol'; items: string[] }
  | { kind: 'table'; head: string[] | null; rows: string[][] }
  | { kind: 'hr' };

const INLINE = /\*\*(.+?)\*\*|`([^`]+)`|\[([^[\]]+)\]|(?<![*\w])\*(?![\s*])([^*]+?)\*(?![*\w])/g;

/** A line's words with their emphasis, code and citations marked. */
export function inlines(text: string): Inline[] {
  const out: Inline[] = [];
  let last = 0;
  for (const m of text.matchAll(INLINE)) {
    if (m.index > last) out.push({ kind: 'text', text: text.slice(last, m.index) });
    if (m[1] !== undefined) out.push({ kind: 'strong', text: m[1] });
    else if (m[2] !== undefined) out.push({ kind: 'code', text: m[2] });
    else if (m[3] !== undefined) out.push({ kind: 'cite', text: m[3] });
    else out.push({ kind: 'em', text: m[4] });
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push({ kind: 'text', text: text.slice(last) });
  return out;
}

const isRow = (line: string) => /^\s*\|.*\|\s*$/.test(line);
const isRule = (line: string) => /^\s*\|[\s:|-]+\|\s*$/.test(line);
const cellsOf = (line: string) => line.trim().slice(1, -1).split('|').map((cell) => cell.trim());
const HEADING = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const ITEM = /^\s*(?:([-*+])|\d+[.)])\s+(.*)$/;
const HR = /^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/;
const opens = (line: string) => isRow(line) || HEADING.test(line) || HR.test(line) || ITEM.test(line);

/** The answer's blocks, in order. A table's first row is its header when a rule line follows it;
 * a list is one kind (bulleted or numbered) for as long as it runs, nesting read flat. */
export function blocks(text: string): Block[] {
  const lines = text.split('\n');
  const out: Block[] = [];
  for (let i = 0; i < lines.length;) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    if (isRow(line)) {
      const rows: string[][] = [];
      let header = false;
      for (; i < lines.length && isRow(lines[i]); i++) {
        if (isRule(lines[i])) header = header || rows.length === 1;
        else rows.push(cellsOf(lines[i]));
      }
      out.push({ kind: 'table', head: header ? rows[0] : null, rows: header ? rows.slice(1) : rows });
    } else if (HR.test(line)) {
      out.push({ kind: 'hr' });
      i++;
    } else if (HEADING.test(line)) {
      const [, marks, words] = line.match(HEADING)!;
      out.push({ kind: 'h', level: marks.length, text: words });
      i++;
    } else if (ITEM.test(line)) {
      const kind = line.match(ITEM)![1] ? 'ul' : 'ol';
      const items: string[] = [];
      for (; i < lines.length; i++) {
        const item = lines[i].match(ITEM);
        if (item && !HR.test(lines[i]) && (item[1] ? 'ul' : 'ol') === kind) items.push(item[2]);
        // A line indented under an item goes on with it.
        else if (!item && /^\s{2,}\S/.test(lines[i]) && items.length) items[items.length - 1] += ` ${lines[i].trim()}`;
        else break;
      }
      out.push({ kind, items });
    } else {
      const start = i;
      while (i < lines.length && lines[i].trim() && (i === start || !opens(lines[i]))) i++;
      out.push({ kind: 'p', text: lines.slice(start, i).join('\n').trim() });
    }
  }
  return out;
}
