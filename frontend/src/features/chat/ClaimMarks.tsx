// What the answer's check said of each statement: ✓ beside one the passages bear out, ⚠ beside
// one they say otherwise or say nothing of. The answer is left as written; a ⚠ opens the
// statement beside what the passages say and the files it was checked against.
import { AlertTriangle, Check } from 'lucide-react';
import type { Schemas } from '../../api/client';
import { cn, shownName } from '../../lib/format';
import { useT } from '../../lib/i18n';

export type Claim = Schemas['Claim'];

/** `text` cut after each claim it holds: the pieces, with the claim's index where its mark goes. */
export function withClaims(text: string, claims: Claim[]): (string | number)[] {
  const cuts = claims
    .map((claim, i) => ({ i, at: claim.text ? text.indexOf(claim.text) : -1, end: 0 }))
    .filter((c) => c.at >= 0)
    .map((c) => ({ ...c, end: c.at + claims[c.i].text.length }))
    .sort((a, b) => a.end - b.end);
  const out: (string | number)[] = [];
  let from = 0;
  for (const { i, end } of cuts) {
    if (end > from) out.push(text.slice(from, end));
    out.push(i);
    from = Math.max(from, end);
  }
  if (from < text.length) out.push(text.slice(from));
  return out;
}

export function ClaimMark({ claim, open, onToggle }: { claim: Claim; open: boolean; onToggle: () => void }) {
  const t = useT();
  if (claim.supported) {
    return <span role="img" aria-label={t('claimSupported')} title={t('claimSupported')} className="mx-0.5 inline-grid align-[-2px] text-ok"><Check className="size-3.5" /></span>;
  }
  const label = t(claim.correction ? 'claimCorrected' : 'claimUnsupported');
  return (
    <button type="button" aria-label={label} title={label} aria-expanded={open} onClick={onToggle}
      className={cn('mx-0.5 inline-grid size-4 place-items-center rounded align-[-3px] text-warn hover:bg-hover', open && 'bg-hover')}>
      <AlertTriangle className="size-3.5" />
    </button>
  );
}

/** The statements the passages do not bear out, each opening to what they say instead. */
export function ClaimDetails({ claims, open, onToggle }: { claims: Claim[]; open: number | null; onToggle: (i: number) => void }) {
  const t = useT();
  const differ = claims.map((claim, i) => ({ claim, i })).filter(({ claim }) => !claim.supported);
  if (!differ.length) return null;
  return (
    <ul className="mt-2 space-y-1 border-t border-line pt-2 text-sm">
      {differ.map(({ claim, i }) => (
        <li key={i}>
          <button type="button" onClick={() => onToggle(i)} aria-expanded={open === i}
            className="flex w-full items-start gap-1.5 text-left text-xs text-muted hover:text-fg">
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-warn" />
            <span className={cn(open !== i && 'line-clamp-1')}>{claim.text}{claim.correction ? ` → ${claim.correction}` : ` · ${t('claimUnsupported')}`}</span>
          </button>
          {open === i && (
            <dl className="mt-1 ml-5 space-y-1 rounded-lg bg-subtle px-3 py-2 text-xs leading-5">
              <dt className="text-faint">{t('claimAsWritten')}</dt>
              {/* struck through only beside what the sources say instead: a statement they say
                  nothing of is not shown as wrong */}
              <dd className={cn(claim.correction && 'line-through decoration-warn/60')}>{claim.text}</dd>
              <dt className="text-faint">{t('claimAsSources')}</dt>
              <dd className="font-medium">{claim.correction ?? t('claimNoSupport')}</dd>
              {!!claim.files?.length && (<>
                <dt className="text-faint">{t('claimCheckedIn')}</dt>
                <dd>{claim.files.map(shownName).join(' · ')}</dd>
              </>)}
            </dl>
          )}
        </li>
      ))}
    </ul>
  );
}
