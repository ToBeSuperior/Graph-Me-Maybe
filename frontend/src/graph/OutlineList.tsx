import { clip, cn, shownName } from '../lib/format';
import { hiddenBy, kindOf, type Outline } from './outline';

/** The outline as an indented list, in reading order -- the order the server sends the nodes in --
 * and every node of it: what the drawing folds away to stay legible, a list has room for. */
export function OutlineList({ outline, showExternal, picked, onPick }: {
  outline: Outline; showExternal: boolean; picked: string | null; onPick: (id: string) => void;
}) {
  const hidden = hiddenBy(outline, undefined, showExternal);
  const kids = new Map<string | null, typeof outline.graph.nodes>();
  for (const n of outline.graph.nodes) {
    if (hidden.has(n.id)) continue;
    const p = outline.parent(n.id);
    const key = p !== undefined && outline.byId.has(p) ? p : null;
    if (!kids.has(key)) kids.set(key, []);
    kids.get(key)!.push(n);
  }
  const rows: { id: string; depth: number; kind: string; text: string }[] = [];
  const seen = new Set<string>();
  const walk = (key: string | null, depth: number): void => (kids.get(key) ?? []).forEach((n) => {
    if (seen.has(n.id)) return;
    seen.add(n.id);
    rows.push({ id: n.id, depth, kind: kindOf(n), text: clip(shownName(n.properties.text ?? n.properties.label), 120) });
    walk(n.id, depth + 1);
  });
  walk(null, 0);
  return (
    <ol className="absolute inset-0 overflow-auto p-3 text-sm">
      {rows.map((r) => (
        <li key={r.id}>
          <button onClick={() => onPick(r.id)} style={{ paddingLeft: 8 + r.depth * 18 }}
            className={cn('flex w-full items-baseline gap-2 truncate rounded py-0.5 pr-2 text-left hover:bg-hover', picked === r.id && 'bg-accent-soft')}>
            <span className="shrink-0 font-mono text-2xs text-faint">{r.kind}</span><span className="truncate">{r.text}</span>
          </button>
        </li>
      ))}
    </ol>
  );
}
