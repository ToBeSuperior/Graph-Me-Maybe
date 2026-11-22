// A made-up server for the browser checks: every /v1 request the app makes is answered here with
// invented documents, so the checks need no server, graph DB or model.
import type { Page, Route } from '@playwright/test';

type Kind = 'box' | 'google' | 'onedrive' | 'dropbox' | 'icloud' | 'synology';

interface Fake { found: Record<Kind, boolean>; workspaces: { active: string; mcp_enabled: boolean; mcp_changes: boolean; items: { id: string; name: string; mcp: boolean }[] }; opened: string[]; originals: string[]; deletedWorkspaces: string[]; connected: Record<Kind, boolean>; boxDrive: string[][]; box: { configured: boolean; connected: boolean; picked: Record<string, string>[]; built: number }; searchModels: Record<string, unknown>[]; installed: string[]; removed: string[]; reset: number; asked: string[]; answered: number; topK: number[]; saved: Record<string, string>[]; cancelled: string[]; refused: number; uploaded: string[]; built: string[][]; starting: number; threads: Record<string, FakeThread>; turns: { question: string; only: string[] | null; exclude: string[] | null; documents: number | null; stamp: string | null }[]; scoped: string[][]; mail: { accounts: Record<string, unknown>[]; connected: Record<string, unknown>[]; read: unknown[]; earlier: number; left: string[] }; watch: Record<string, unknown>[]; watchBuilt: string[]; watchFolders: { path: string; kind: string }[]; deletedDocs: string[]; ungrouped: string[] }


/** A conversation as the server keeps it. */
interface FakeThread { id: string; title: string; scope: string[] | null; workspace: string; turns: Record<string, unknown>[]; created_at: number; updated_at: number }

/** A job as `GET /v1/jobs` lists it, newest first. */
type FakeJob =Record<string, unknown> & { id: string; kind: string; state: string };

const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64');

const docs = [
  { id: 'spec', label: 'SPEC-1_가상 규격서.docx', title: null, mentions: 0, modified_at: 1767225600 },
  { id: 'proc', label: 'PROC-2_가상 절차서.docx', title: null, mentions: 1, modified_at: 1767312000 },
  { id: 'deck', label: '가상 보고.pptx', title: null, mentions: 1, modified_at: 1767398400 },
];

const sources = [
  { id: 'proc:0', score: 0.91, document_id: 'proc', file: docs[1].label, members: ['p1'], modified: 1767312000,
    context: `${docs[1].label}\n시험 압력은 SPEC-1에 따른다.`,
    linked: [
      { document_id: 'spec', file: docs[0].label, hop: 2, via: 'proc', direction: 'cites', modified: 1767225600,
        passages: [{ id: 'spec:0', score: 0.8, members: ['s1'], text: `${docs[0].label}\n시험 압력은 1.0 MPa이다.` }] },
      { document_id: 'deck', file: docs[2].label, hop: 3, via: 'spec', direction: 'similar', modified: 1767398400, passages: [] },
    ] },
];

const BUILD_STARTED = { id: 'bx', kind: 'build', state: 'reading', done: 0, total: 1, source: 'Box', failed: [], documents: 0,
  known: 0, carried: 0, links: 0, skipped: 0, embedded: 0, error: null };

export async function fakeApi(page: Page,
  { box = true, boxDrive = true, googleDrive = true, oneDrive = true, dropbox = true, workspaces = true, empty = false, slow = 0, samples = true, jobs = [] as FakeJob[], key = '', reading = [] as Record<string, unknown>[],
    progress = null as Record<string, unknown> | null, elapsed = null as number | null, starting = 0,
    problems = null as string[] | null, mailWorkspace = false, mailRefused = '', mailDoc = false, gone = false, pageCount = 2, versions = false } = {}): Promise<Fake> {
  const fake: Fake = {
    searchModels: [
      { name: 'general', model: 'harrier-oss-v1-0.6b', size: 639448320, installed: false, chosen: true,
        fetching: { done: 319724160, total: 639448320 }, error: null },
      { name: 'korean', model: 'KURE-v1', size: 437779008, installed: true, chosen: false, fetching: null, error: null },
    ],
    workspaces: { active: 'default', mcp_enabled: true, mcp_changes: false, items: [{ id: 'default', name: 'Default', mcp: false }, { id: 'w2', name: 'Invented audits', mcp: false }] },
    found: { box: boxDrive, google: googleDrive, onedrive: oneDrive, dropbox: dropbox, icloud: false, synology: false }, opened: [], originals: [], deletedWorkspaces: [],
    connected: { box: false, google: false, onedrive: false, dropbox: false, icloud: false, synology: false }, boxDrive: [], box: { configured: box, connected: box, picked: [], built: 0 },
    installed: [], removed: [], reset: 0, asked: [], answered: 0, topK: [], saved: [], cancelled: [], refused: 0,
    uploaded: [], built: [], starting, threads: {}, turns: [], scoped: [], mail: { accounts: [], connected: [], read: [], earlier: 0, left: [] }, watch: [], watchBuilt: [], watchFolders: [], deletedDocs: [], ungrouped: [] };
  if (mailWorkspace) fake.workspaces.items.push({ id: 'm1', name: 'kim@invented.test', mcp: false, kind: 'mail', account: 'a1' } as never);
  if (mailWorkspace) fake.workspaces.active = 'm1';
  // A revised version of the procedure, folded under it until it is ungrouped.
  const revised = { document_id: 'proc-new', file: 'PROC-2_가상 절차서 개정.docx', modified: 1767398400, latest: true };
  const folded = () => (versions && !fake.ungrouped.length ? [revised] : []);
  const json = (route: Route, body: unknown, status = 200) =>
    route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route('**/v1/**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method();
    // A server with a key (GRAPHME_API_KEY) answers nothing without it; the web app sends it as a cookie.
    const cookies = (await route.request().allHeaders()).cookie?.split(/;\s*/) ?? [];
    if (key && !cookies.includes(`graphme_key=${key}`)) {
      fake.refused += 1;
      return json(route, { detail: 'API 키가 필요합니다 (GRAPHME_API_KEY)' }, 401);
    }
    const boxFolders: Record<string, { name: string; path: { id: string; name: string }[]; items: { id: string; type: string; name: string; size?: number }[] }> = {
      0: { name: 'All Files', path: [], items: [
        { id: '10', type: 'folder', name: 'Specs' },
        { id: 'f1', type: 'file', name: 'Leak Test Procedure.pdf', size: 120000 }] },
      10: { name: 'Specs', path: [{ id: '0', name: 'All Files' }], items: [
        { id: 'f2', type: 'file', name: 'Valve Spec.docx', size: 64000 }] },
    };
    // Graphs kept apart: one open at a time.
    const listed = () => (workspaces ? { available: true, ...fake.workspaces } : { available: false, items: [] });
    if (path === '/v1/workspaces' && method === 'GET') return json(route, listed());
    if (path === '/v1/workspaces' && method === 'POST') {
      if (!workspaces) return json(route, { detail: 'no workspaces' }, 409);
      const made = { id: `w${fake.workspaces.items.length + 1}`, name: route.request().postDataJSON().name, mcp: false };
      fake.workspaces.items.push(made);
      return json(route, made);
    }
    if (path === '/v1/mcp' && method === 'PUT') { Object.assign(fake.workspaces, Object.fromEntries(Object.entries({ mcp_enabled: route.request().postDataJSON().enabled, mcp_changes: route.request().postDataJSON().changes }).filter(([, v]) => v !== undefined))); return json(route, listed()); }
    const ws = path.match(/^\/v1\/workspaces\/([^/]+)(\/open)?$/);
    if (ws) {
      const item = fake.workspaces.items.find((i) => i.id === ws[1])!;
      if (ws[2]) { fake.opened.push(ws[1]); fake.workspaces.active = ws[1]; return json(route, listed()); }
      if (method === 'PATCH') { Object.assign(item, route.request().postDataJSON()); return json(route, item); }
      if (method === 'DELETE') {
        if (ws[1] === fake.workspaces.active) return json(route, { detail: 'the workspace open now cannot be deleted' }, 409);
        fake.deletedWorkspaces.push(ws[1]);
        fake.workspaces.items = fake.workspaces.items.filter((i) => i.id !== ws[1]);
        return json(route, listed());
      }
    }
    // A cloud drive's desktop client on this machine: Box Drive, Google Drive for desktop.
    const usage = path.match(/^\/v1\/drives\/(box|google|onedrive|dropbox|icloud|synology)\/usage$/);
    if (usage) return json(route, { bytes: 991_900_000, files: 102 });
    const desk = path.match(/^\/v1\/drives\/(box|google|onedrive|dropbox|icloud|synology)(\/connect|\/folders)?$/);
    if (desk) {
      const kind = desk[1] as Kind;
      const top = { box: 'Box-Box', google: 'GoogleDrive-me@invented.test', onedrive: 'CloudStorage', dropbox: 'Dropbox', icloud: 'iCloud Drive', synology: 'SynologyDrive' }[kind];
      const found = fake.found[kind];
      if (desk[2] === '/connect') fake.connected[kind] = true;
      else if (!desk[2] && method === 'DELETE') fake.connected[kind] = false;
      if (desk[2] !== '/folders') {
        return json(route, found ? { found: true, root: `/Users/me/Library/CloudStorage/${top}`, connected: fake.connected[kind] }
          : { found: false, root: null, connected: false });
      }
      const at = url.searchParams.get('path') ?? '';
      const listed: Record<string, unknown> = {
        '': { id: '', name: top, path: [], items: [
          { id: 'Quality', type: 'folder', name: 'Quality', picked: false, readable: true, modified: 1767225600 },
          { id: 'Plan.pdf', type: 'file', name: 'Plan.pdf', picked: false, readable: true, size: 2400000, modified: 1767225600 },
          { id: 'notes.txt', type: 'file', name: 'notes.txt', picked: false, readable: false, size: 1200, modified: 1767225600 }] },
        Quality: { id: 'Quality', name: 'Quality', path: [{ id: '', name: top }], items: [
          { id: 'Quality/Audit.docx', type: 'file', name: 'Audit.docx', picked: false, readable: true, size: 64000, modified: 1767225600 }] },
      };
      return json(route, listed[at]);
    }
    // Mail accounts: connecting counts the mailbox and reads nothing; the reader is then asked.
    if (path === '/v1/mail/accounts' && method === 'GET') return json(route, fake.mail.accounts);
    if (path === '/v1/watch' && method === 'GET') return json(route, fake.watch);
    if (path === '/v1/watch/folders') return json(route, fake.watchFolders);
    const deletedDoc = path.match(/^\/v1\/documents\/([^/]+)$/);
    if (deletedDoc && method === 'DELETE') {
      fake.deletedDocs.push(deletedDoc[1]);
      for (const t of fake.watch) t.missing = (t.missing as { document_id: string }[]).filter((m) => m.document_id !== deletedDoc[1]);
      return json(route, { deleted: deletedDoc[1] });
    }
    const watched = path.match(/^\/v1\/watch\/(folder|drive|mail)\/([^/]+)(\/build)?$/);
    if (watched && method === 'POST') { fake.watchBuilt.push(watched[2]); return json(route, { ...BUILD_STARTED, source: 'watch' }, 202); }
    if (watched && method === 'DELETE') { fake.watch = fake.watch.filter((t) => t.id !== watched[2]); return json(route, fake.watch); }
    if (watched && method === 'PUT') {
      const { mode, at, path: folder } = route.request().postDataJSON();
      let kept = fake.watch.find((t) => t.id === watched[2]);
      if (!kept) {
        kept = { kind: watched[1], id: watched[2], workspace: 'default', path: folder, name: folder.split('/').pop(), changed: 0, missing: [], building: false, error: null };
        fake.watch.push(kept);
      }
      Object.assign(kept, { mode, at, next_build: mode === 'daily' ? `2026-05-05T${at}:00` : null });
      return json(route, kept);
    }
    // A Google sign-in: the browser finishes it, and the account is on the server at the next ask.
    if (path === '/v1/mail/microsoft/connect' && method === 'POST') {
      fake.mail.accounts.push({ id: 'o1', kind: 'outlook', host: 'graph.microsoft.com', port: 443, address: 'lee@invented.test',
        workspace: 'm2', state: 'counted', count: { messages: 870, attachments: 64, attachment_bytes: null, oldest: '2023-06-01' },
        read_since: null, last_checked: null, error: null });
      return json(route, { url: 'https://login.microsoftonline.com/common/oauth2/v2.0/authorize?invented' });
    }
    if (path === '/v1/mail/google/connect' && method === 'POST') {
      fake.mail.accounts.push({ id: 'g1', kind: 'gmail', host: 'gmail.googleapis.com', port: 443, address: 'kim@invented.test',
        workspace: 'm1', state: 'counted', count: { messages: 3200, attachments: 410, attachment_bytes: null, oldest: '2021-04-02' },
        read_since: null, last_checked: null, error: null });
      return json(route, { url: 'https://accounts.google.com/o/oauth2/v2/auth?invented' });
    }
    if (path === '/v1/mail/accounts' && method === 'POST') {
      if (mailRefused) return json(route, { detail: mailRefused }, 400);
      const body = route.request().postDataJSON();
      fake.mail.connected.push(body);
      fake.mail.accounts.push({ id: 'a1', kind: 'imap', host: body.host, port: body.port, address: body.address, workspace: 'm1',
        state: 'counted', count: { messages: 1240, attachments: 96, attachment_bytes: 38_000_000, oldest: '2021-03-04' } });
      return json(route, fake.mail.accounts[0]);
    }
    const mailRead = path.match(/^\/v1\/mail\/accounts\/([^/]+)\/(read|earlier)$/);
    if (mailRead) {
      if (mailRead[2] === 'read') {
        const { since } = route.request().postDataJSON();
        fake.mail.read.push(since);
        Object.assign(fake.mail.accounts[0], { state: 'current', read_since: since });
      } else fake.mail.earlier += 1;
      return json(route, { ...BUILD_STARTED, source: 'mail' }, 202);
    }
    const mailAccount = path.match(/^\/v1\/mail\/accounts\/([^/]+)$/);
    if (mailAccount && method === 'DELETE') {
      fake.mail.left.push(`${mailAccount[1]}:${url.searchParams.get('workspace')}`);
      fake.mail.accounts = [];
      return json(route, []);
    }
    if (path === '/v1/documents/msg/graph') {
      return json(route, { nodes: [
        { id: 'msg', labels: ['DocNode', 'Document'], properties: { label: 'invoice.eml', meta_mail_message_id: '<1@invented.test>',
          meta_mail_from: 'Lee <lee@invented.test>', meta_mail_to: ['kim@invented.test'], meta_mail_date: '2026-01-05T09:00:00+09:00',
          meta_mail_subject: 'Invented valve order' } },
        { id: 'b1', labels: ['DocNode', 'TextChunk'], properties: { label: 'b1', text: 'Please confirm the invented order.', meta_body_index: 0 } }],
        relationships: [], chunks: [], pages: {}, crops: [] });
    }
    if (path === '/v1/jobs/build' && method === 'POST') {
      // A drive's build names paths; an upload's, the files it uploaded.
      const { paths, files } = route.request().postDataJSON();
      if (files?.length) { fake.built.push(files); return json(route, { ...BUILD_STARTED, source: 'upload', total: files.length }, 202); }
      fake.boxDrive.push(paths ?? []);
      return json(route, { ...BUILD_STARTED, source: 'Box Drive' }, 202);
    }
    if (path === '/v1/cloud/box') return json(route, { configured: fake.box.configured, connected: fake.box.connected, picked: fake.box.picked });
    if (path === '/v1/cloud/box/connect') return json(route, { url: 'https://account.box.invented/api/oauth2/authorize?state=x' });
    if (path === '/v1/cloud/box/picked') {
      fake.box.picked = route.request().postDataJSON().items;
      return json(route, { configured: true, connected: true, picked: fake.box.picked });
    }
    const boxFolder = path.match(/^\/v1\/cloud\/box\/folders\/(.+)$/);
    if (boxFolder) {
      const folder = boxFolders[boxFolder[1]];
      return json(route, { id: boxFolder[1], ...folder, items: folder.items.map((i) => ({
        ...i, picked: fake.box.picked.some((p) => p.id === i.id && p.type === i.type) })) });
    }
    if (path === '/v1/cloud/box/build') { fake.box.built += 1; return json(route, { ...BUILD_STARTED }, 202); }
    const searchModel = path.match(/^\/v1\/search-models\/([^/]+)(\/install)?$/);
    if (path === '/v1/search-models') return json(route, fake.searchModels);
    if (searchModel) {
      const row = fake.searchModels.find((m) => m.name === searchModel[1])!;
      if (method === 'DELETE') { fake.removed.push(searchModel[1]); row.installed = false; }
      else { fake.installed.push(searchModel[1]); row.fetching = { done: 0, total: row.size }; }
      return json(route, fake.searchModels, method === 'DELETE' ? 200 : 202);
    }
    if (path === '/v1/settings') {
      if (method === 'PUT') {
        const values = route.request().postDataJSON().values;
        fake.saved.push(values);
        if (values.GRAPHME_EMBEDDING_PROVIDER) {
          for (const m of fake.searchModels) m.chosen = m.name === values.GRAPHME_EMBEDDING_PROVIDER;
        }
      }
      const key = !!fake.saved.length;
      return json(route, { endpoint: 'openai', samples, problems: key ? [] : problems ?? ['The LLM API key is empty'], fields: [
        { env: 'OPENAI_API_KEY', secret: true, set: key, value: null, hint: key ? 'abcd' : null },
        { env: 'GRAPHME_PROVIDER', secret: false, set: true, value: 'openai', hint: null,
          choices: { openai: 'OPENAI_API_KEY', anthropic: 'ANTHROPIC_API_KEY', gemini: 'GEMINI_API_KEY' } },
        { env: 'GRAPHME_MODEL', secret: false, set: true, value: 'gpt-x', hint: null },
      ] });
    }
    // The graph DB still starting: the server answers 503 with Retry-After (`GraphStarting`).
    if (path === '/v1/graph/map' && fake.starting > 0) {
      fake.starting -= 1;
      return route.fulfill({ status: 503, contentType: 'application/json', headers: { 'Retry-After': '1' },
        body: JSON.stringify({ detail: 'The graph DB is starting' }) });
    }
    if (path === '/v1/graph/map') {
      return json(route, empty || fake.reset ? { documents: [], total: 0, links: [], similar: [] }
        : { documents: mailDoc ? [...docs, { id: 'msg', label: 'invoice.eml', title: null, mentions: 1, modified_at: 1767225600 }] : docs, total: docs.length,
            links: [{ from: 'proc', to: 'spec', count: 1, matched_by: ['number'] }, ...(mailDoc ? [{ from: 'msg', to: 'spec', count: 1, matched_by: ['embedded'] }, { from: 'msg', to: 'proc', count: 1, matched_by: ['number'] }] : [])], similar: [] });
    }
    if (path === '/v1/jobs') return json(route, jobs);
    if (path === '/v1/files' && method === 'POST') {
      // The form's file part names the file; nothing of it is read.
      const name = (route.request().postData() ?? '').match(/filename="([^"]+)"/)?.[1]?.split('/').pop() ?? '';
      fake.uploaded.push(name);
      return json(route, { id: `up${fake.uploaded.length}` });
    }
    const cancel = path.match(/^\/v1\/jobs\/([^/]+)\/cancel$/);
    if (cancel && method === 'POST') {
      fake.cancelled.push(cancel[1]);
      return json(route, { ...jobs.find((j) => j.id === cancel[1]), state: 'cancelled' });
    }
    if (path === '/v1/documents') {
      const listed = versions && fake.ungrouped.length ? [...docs, { id: revised.document_id, label: revised.file, modified_at: revised.modified }] : docs;
      return json(route, { space_ok: true, items: empty || fake.reset ? [] : listed.map((d) => ({
        name: d.label, format: d.label.split('.').pop(), in_space: true, document_id: d.id, file_id: `file-${d.id}`,
        state: 'idle', modified: d.modified_at, versions: d.id === 'proc' ? folded() : [] })).concat(reading as never[]) });
    }
    const ungroup = path.match(/^\/v1\/documents\/([^/]+)\/ungroup$/);
    if (ungroup && method === 'POST') {
      fake.ungrouped.push(ungroup[1]);
      return json(route, { ungrouped: ungroup[1], pairs: 1 });
    }
    // Conversations: a turn is streamed as the server streams it -- found, answered, checked, saved.
    if (path === '/v1/threads' && method === 'GET') {
      return json(route, Object.values(fake.threads).reverse().map((t) => ({ id: t.id, title: t.title, updated_at: t.updated_at, turns: t.turns.length })));
    }
    if (path === '/v1/threads' && method === 'POST') {
      const body = route.request().postDataJSON() ?? {};
      const id = `t${Object.keys(fake.threads).length + 1}`;
      fake.threads[id] = { id, title: '', scope: body.scope ?? null, workspace: body.workspace ?? fake.workspaces.active, turns: [], created_at: 1, updated_at: 1 };
      return json(route, fake.threads[id]);
    }
    const thread = path.match(/^\/v1\/threads\/([^/]+)(\/turns)?$/);
    if (thread) {
      const kept = fake.threads[thread[1]];
      if (!kept) return json(route, { detail: 'this conversation cannot be found' }, 404);
      if (!thread[2] && method === 'GET') return json(route, kept);
      if (!thread[2] && method === 'PATCH') { Object.assign(kept, route.request().postDataJSON()); return json(route, kept); }
      if (!thread[2] && method === 'DELETE') { delete fake.threads[kept.id]; return json(route, { deleted: kept.id }); }
      const { question, documents, stamp, exclude } = route.request().postDataJSON();
      fake.turns.push({ question, only: kept.scope, exclude: exclude ?? null, documents: documents ?? null, stamp: stamp ?? null });
      fake.asked.push(question);
      fake.topK.push(documents);
      if (slow) await new Promise((r) => setTimeout(r, slow));
      // A message found first, its name carrying the digest that tells two of one subject apart.
      const message = { id: 'msg:0', score: 0.95, document_id: 'msg', file: 'Invented valve order.emb0a1b2c3d.eml',
        members: ['b1'], modified: 1767225600, context: 'Invented valve order\nPlease find the order attached.', linked: [] };
      const found = { sources: (mailDoc ? [message, ...sources] : sources).map((x) => (x.document_id === 'proc' ? { ...x, versions: folded() } : x)).filter((x) => (!kept.scope || kept.scope.includes(x.document_id)) && !exclude?.includes(x.document_id)), queries: [question], text_only: false };
      fake.answered += 1;
      const answer = { ...found, answer: `시험 압력은 **1.0 MPa**이다. [${docs[0].label}]\n| 항목 | 기준 |\n|---|---|\n| 압력 | 1.0 MPa |\n\n유지 시간은 5분이다. [${docs[1].label}]`, claims: [] };
      const checked = { checked: true, claims: [
        { text: '시험 압력은 **1.0 MPa**이다.', supported: true, correction: null, files: [docs[0].label] },
        { text: '유지 시간은 5분이다.', supported: false, correction: '유지 시간은 10분이다.', files: [docs[1].label] }] };
      const saved = { question, scope: kept.scope, found, answer: { ...answer, ...checked }, stamp: stamp ?? null, asked_at: 2, error: null };
      const event = (name: string, data: unknown) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`;
      const body = found.sources.length
        ? event('searched', found) + event('answered', answer) + event('checked', checked) + event('saved', saved)
        : event('searched', found) + event('saved', { ...saved, answer: null });
      try {
        await route.fulfill({ status: 200, contentType: 'text/event-stream', body });
      } catch { return; } // the page stopped it
      kept.turns.push(found.sources.length ? saved : { ...saved, answer: null });
      kept.title ||= question;
      return;
    }
    // Files on this computer as documents of the graph: a path naming "New" is in none until built.
    if (path === '/v1/scope' && method === 'POST') {
      const { paths } = route.request().postDataJSON() as { paths: string[] };
      fake.scoped.push(paths);
      // "Audit" is held by the other workspace, "New" by none until a build; the rest is the spec.
      const audit = paths.filter((p) => p.includes('Audit'));
      if (audit.length === paths.length) return json(route, { document_ids: ['proc'], missing: [], elsewhere: [], workspace: 'w2', build_into: 'w2' });
      const missing = paths.filter((p) => p.includes('New') && !fake.boxDrive.length);
      const held = paths.filter((p) => !audit.includes(p) && !missing.includes(p));
      return json(route, { document_ids: held.map((p) => (p.includes('New') ? 'deck' : 'spec')), missing,
        elsewhere: audit.map((p) => ({ path: p, workspace: 'w2' })), workspace: fake.workspaces.active, build_into: fake.workspaces.active });
    }
    if (path === '/v1/graph/reset' && method === 'POST') {
      fake.reset += 1;
      return json(route, { deleted: docs.length });
    }
    const original = path.match(/^\/v1\/documents\/([^/]+)\/open$/);
    if (original && method === 'POST') { fake.originals.push(original[1]); return json(route, { opened: docs.find((d) => d.id === original[1])!.label }); }
    const outline = path.match(/^\/v1\/documents\/([^/]+)\/graph$/);
    if (outline) {
      const doc = docs.find((d) => d.id === outline[1])!;
      return json(route, { nodes: [{ id: doc.id, labels: ['Document'], properties: { label: doc.label } }],
        relationships: [], chunks: [], pages: {}, crops: [] });
    }
    const file = path.match(/^\/v1\/documents\/(.+)\/file$/);
    if (file && method === 'POST') return json(route, { file_id: `file-${file[1]}` });
    if (/^\/v1\/files\/[^/]+\/pages$/.test(path)) {
      return json(route, { source: 'x.docx', format: 'docx', state: progress ? 'reading' : 'done', error: null,
        pending: !!progress, progress, elapsed,
        sheets: Array.from({ length: pageCount }, (_, i) => i + 1).map((index) => ({ index, name: null, width: 800, height: 1100, derived: [], seconds: 1, pending: false, error: null })) });
    }
    if (/^\/v1\/files\/[^/]+\/graph$/.test(path)) {
      return json(route, { nodes: [], relationships: [], chunks: [], pages: { s1: 2, p1: 1 }, crops: [] });
    }
    if (/\/image$/.test(path) && gone) return json(route, { detail: "this document's source file cannot be found" }, 404);
    if (/\/image$/.test(path)) return route.fulfill({ status: 200, contentType: 'image/png', body: PNG });
    return json(route, { detail: `not faked: ${method} ${path}` }, 404);
  });
  return fake;
}
