// A made-up server for the browser checks: every /v1 request the app makes is answered here with
// invented documents, so the checks need no server, graph DB or model.
import type { Page, Route } from '@playwright/test';

interface Fake { found: { box: boolean; google: boolean }; workspaces: { active: string; items: { id: string; name: string }[] }; opened: string[]; deletedWorkspaces: string[]; connected: { box: boolean; google: boolean }; boxDrive: string[][]; box: { configured: boolean; connected: boolean; picked: Record<string, string>[]; built: number }; searchModels: Record<string, unknown>[]; installed: string[]; removed: string[]; reset: number; asked: string[]; answered: number; topK: number[]; saved: Record<string, string>[]; cancelled: string[]; refused: number }

/** A job as `GET /v1/jobs` lists it, newest first. */
type FakeJob = Record<string, unknown> & { id: string; kind: string; state: string };

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
  { box = true, boxDrive = true, googleDrive = true, workspaces = true, empty = false, slow = 0, samples = true, jobs = [] as FakeJob[], key = '', reading = [] as Record<string, unknown>[],
    progress = null as Record<string, unknown> | null, elapsed = null as number | null } = {}): Promise<Fake> {
  const fake: Fake = {
    searchModels: [
      { name: 'general', model: 'harrier-oss-v1-0.6b', size: 639448320, installed: false, chosen: true,
        fetching: { done: 319724160, total: 639448320 }, error: null },
      { name: 'korean', model: 'KURE-v1', size: 437779008, installed: true, chosen: false, fetching: null, error: null },
    ],
    workspaces: { active: 'default', items: [{ id: 'default', name: 'Default' }, { id: 'w2', name: 'Invented audits' }] },
    found: { box: boxDrive, google: googleDrive }, opened: [], deletedWorkspaces: [],
    connected: { box: false, google: false }, boxDrive: [], box: { configured: box, connected: box, picked: [], built: 0 },
    installed: [], removed: [], reset: 0, asked: [], answered: 0, topK: [], saved: [], cancelled: [], refused: 0 };
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
      const made = { id: `w${fake.workspaces.items.length + 1}`, name: route.request().postDataJSON().name };
      fake.workspaces.items.push(made);
      return json(route, made);
    }
    const ws = path.match(/^\/v1\/workspaces\/([^/]+)(\/open)?$/);
    if (ws) {
      const item = fake.workspaces.items.find((i) => i.id === ws[1])!;
      if (ws[2]) { fake.opened.push(ws[1]); fake.workspaces.active = ws[1]; return json(route, listed()); }
      if (method === 'PATCH') { item.name = route.request().postDataJSON().name; return json(route, item); }
      if (method === 'DELETE') {
        if (ws[1] === fake.workspaces.active) return json(route, { detail: 'the workspace open now cannot be deleted' }, 409);
        fake.deletedWorkspaces.push(ws[1]);
        fake.workspaces.items = fake.workspaces.items.filter((i) => i.id !== ws[1]);
        return json(route, listed());
      }
    }
    // A cloud drive's desktop client on this machine: Box Drive, Google Drive for desktop.
    const desk = path.match(/^\/v1\/drives\/(box|google)(\/connect|\/folders)?$/);
    if (desk) {
      const kind = desk[1] as 'box' | 'google';
      const top = kind === 'box' ? 'Box-Box' : 'GoogleDrive-me@invented.test';
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
    if (path === '/v1/jobs/build' && method === 'POST') {
      fake.boxDrive.push(route.request().postDataJSON().paths ?? []);
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
      return json(route, { endpoint: 'openai', samples, problems: key ? [] : ['The LLM API key is empty'], fields: [
        { env: 'OPENAI_API_KEY', secret: true, set: key, value: null, hint: key ? 'abcd' : null },
        { env: 'GRAPHME_PROVIDER', secret: false, set: true, value: 'openai', hint: null,
          choices: { openai: 'OPENAI_API_KEY', anthropic: 'ANTHROPIC_API_KEY', gemini: 'GEMINI_API_KEY' } },
        { env: 'GRAPHME_MODEL', secret: false, set: true, value: 'gpt-x', hint: null },
      ] });
    }
    if (path === '/v1/graph/map') {
      return json(route, empty || fake.reset ? { documents: [], total: 0, links: [], similar: [] }
        : { documents: docs, total: docs.length, links: [{ from: 'proc', to: 'spec', count: 1, matched_by: ['number'] }], similar: [] });
    }
    if (path === '/v1/jobs') return json(route, jobs);
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
      fake.topK.push(route.request().postDataJSON().top_k);
      if (slow) await new Promise((r) => setTimeout(r, slow));
      return json(route, { sources });
    }
    if (path === '/v1/ask') {
      fake.answered += 1;
      return json(route, { answer: `시험 압력은 **1.0 MPa**이다. [${docs[0].label}]\n| 항목 | 기준 |\n|---|---|\n| 압력 | 1.0 MPa |`, sources });
    }
    if (path === '/v1/graph/reset' && method === 'POST') {
      fake.reset += 1;
      return json(route, { deleted: docs.length });
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
