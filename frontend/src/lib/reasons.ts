// Why a document failed is written by the server in English, one of a few sentences
// (`server/jobs.py` and the build's own); each is said here in the chosen language.
import type { T, WordKey } from './i18n';

const KNOWN: [string, WordKey][] = [
  ['the file is locked by a password', 'whyProtected'],
  ['the pages could not be drawn', 'whyUndrawn'],
  ['could not be read', 'whyUnread'],
  ["this document's source file cannot be found", 'whyGone'],
  ['this document cannot be found', 'whyGone'],
  ['the folder did not list in time', 'whyUnlisted'],
  ['not read in time', 'whyLate'],
  ['no longer readable in Google Drive', 'whyLostDrive'],
  ['no longer readable in Box', 'whyLostBox'],
];

/** `why` in the chosen language; a reason not known here as the server wrote it. */
export function reason(t: T, why: string): string {
  const known = KNOWN.find(([start]) => why.startsWith(start));
  return known ? t(known[1] as never) : why;
}
