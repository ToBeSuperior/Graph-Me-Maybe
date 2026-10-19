// Which of the files a question found are to be downloaded: one file, one hop's or all of them
// picked or not, and the link that downloads them (`GET /v1/documents/files.zip`).

/** `picked` with `ids` turned on or off. */
export function toggled(picked: Set<string>, ids: string[], on: boolean): Set<string> {
  const next = new Set(picked);
  for (const id of ids) {
    if (on) next.add(id);
    else next.delete(id);
  }
  return next;
}

/** Whether every one of `ids` is picked, some, or none -- a hop's box shows which. */
export function groupState(picked: Set<string>, ids: string[]): 'all' | 'some' | 'none' {
  const on = ids.filter((id) => picked.has(id)).length;
  return on === 0 ? 'none' : on === ids.length ? 'all' : 'some';
}

/** The download of `files`, each in its folder, in the order given. A link, so the app's own
 * window saves it as it saves any download. */
export function filesLink(files: { id: string; folder: string }[], name: string): string {
  const query = new URLSearchParams();
  for (const { id, folder } of files) {
    query.append('doc', id);
    query.append('folder', folder);
  }
  query.set('name', name);
  return `/v1/documents/files.zip?${query}`;
}
