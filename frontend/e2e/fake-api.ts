// A made-up server for the browser checks: every /v1 request the app makes is answered here with
// invented documents, so the checks need no server, graph DB or model.
import type { Page, Route } from '@playwright/test';

type Kind = 'box' | 'google' | 'onedrive' | 'dropbox' | 'icloud' | 'synology';

interface Fake { found: Record<Kind, boolean>; workspaces: { active: string; mcp_enabled: boolean; mcp_changes: boolean; items: { id: string; name: string; mcp: boolean }[] }; opened: string[]; originals: string[]; deletedWorkspaces: string[]; connected: Record<Kind, boolean>; boxDrive: string[][]; box: { configured: boolean; connected: boolean; picked: Record<string, string>[]; built: number }; searchModels: Record<string, unknown>[]; installed: string[]; removed: string[]; reset: number; asked: string[]; answered: number; topK: number[]; saved: Record<string, string>[]; cancelled: string[]; refused: number; uploaded: string[]; built: string[][]; starting: number; mail: { accounts: Record<string, unknown>[]; connected: Record<string, unknown>[]; read: unknown[]; earlier: number; left: string[] } }


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
    problems = null as string[] | null, mailWorkspace = false, mailRefused = '', mailDoc = false } = {}): Promise<Fake> {
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
    uploaded: [], built: [], starting, mail: { accounts: [], connected: [], read: [], earlier: 0, left: [] } };
  if (mailWorkspace) fake.workspaces.items.push({ id: 'm1', name: 'kim@invented.test', mcp: false, kind: 'mail', account: 'a1' } as never);
  if (mailWorkspace) fake.workspaces.active = 'm1';
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
    // A Google sign-in: the browser finishes it, and the account is on the server at the next ask.
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
      return json(route, { space_ok: true, items: empty || fake.reset ? [] : docs.map((d) => ({
        name: d.label, format: d.label.split('.').pop(), in_space: true, document_id: d.id, file_id: `file-${d.id}`,
        state: 'idle', modified: d.modified_at })).concat(reading as never[]) });
    }
    if (path === '/v1/search') {
      fake.asked.push(route.request().postDataJSON().question);
      fake.topK.push(route.request().postDataJSON().documents);
      if (slow) await new Promise((r) => setTimeout(r, slow));
      // A message found first, its name carrying the digest that tells two of one subject apart.
      const message = { id: 'msg:0', score: 0.95, document_id: 'msg', file: 'Invented valve order.emb0a1b2c3d.eml',
        members: ['b1'], modified: 1767225600, context: 'Invented valve order\nPlease find the order attached.', linked: [] };
      return json(route, { sources: mailDoc ? [message, ...sources] : sources });
    }
    if (path === '/v1/ask') {
      fake.answered += 1;
      return json(route, { answer: `시험 압력은 **1.0 MPa**이다. [${docs[0].label}]\n| 항목 | 기준 |\n|---|---|\n| 압력 | 1.0 MPa |`, sources });
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
        sheets: [1, 2].map((index) => ({ index, name: null, width: 800, height: 1100, derived: [], seconds: 1, pending: false, error: null })) });
    }
    if (/^\/v1\/files\/[^/]+\/graph$/.test(path)) {
      return json(route, { nodes: [], relationships: [], chunks: [], pages: { s1: 2, p1: 1 }, crops: [] });
    }
    if (/\/image$/.test(path)) return route.fulfill({ status: 200, contentType: 'image/png', body: PNG });
    return json(route, { detail: `not faked: ${method} ${path}` }, 404);
  });
  return fake;
}
