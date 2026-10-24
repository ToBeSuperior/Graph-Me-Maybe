// A made-up server for the browser checks: every /v1 request the app makes is answered here with
// invented documents, so the checks need no server, graph DB or model.
import type { Page, Route } from '@playwright/test';

interface Fake { reset: number; asked: string[]; answered: number; topK: number[]; saved: Record<string, string>[]; cancelled: string[]; refused: number; drive: { configured: boolean; connected: boolean; picked: number; built: number } }

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

export async function fakeApi(page: Page,
  { empty = false, slow = 0, samples = true, jobs = [] as FakeJob[], key = '', reading = [] as Record<string, unknown>[],
    progress = null as Record<string, unknown> | null, elapsed = null as number | null } = {}): Promise<Fake> {
  const fake: Fake = { reset: 0, asked: [], answered: 0, topK: [], saved: [], cancelled: [], refused: 0,
    drive: { configured: true, connected: false, picked: 0, built: 0 } };
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
    if (path === '/v1/settings') {
      if (method === 'PUT') fake.saved.push(route.request().postDataJSON().values);
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
    if (path === '/v1/cloud/google') {
      if (method === 'DELETE') Object.assign(fake.drive, { connected: false, picked: 0 });
      const { configured, connected, picked } = fake.drive;
      return json(route, { configured, connected, picked });
    }
    if (path === '/v1/cloud/google/connect') {
      // The browser signs in and picks two files; the answer lands on the server meanwhile.
      Object.assign(fake.drive, { connected: true, picked: 2 });
      return json(route, { url: 'https://accounts.google.invented/o/oauth2/v2/auth?trigger_onepick=true' });
    }
    if (path === '/v1/cloud/google/build') {
      fake.drive.built += 1;
      return json(route, { id: 'drive-build', kind: 'build', state: 'reading', source: 'Google Drive' }, 202);
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
