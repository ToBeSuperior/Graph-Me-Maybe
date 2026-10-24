// The app as a reader meets it, against the fake API: an empty graph invites a folder; a
// question gathers folders, its answer's citation opens the cited document; the tree answers to
// the keyboard; the graph is reset only once the word is typed.
import { expect, test } from '@playwright/test';
import { fakeApi } from './fake-api';

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('lang', 'ko'));
});

test('an empty graph invites a folder or the samples', async ({ page }) => {
  await fakeApi(page, { empty: true });
  await page.goto('/');
  await expect(page.getByText('문서 폴더를 끌어다 놓으세요')).toBeVisible();
  await expect(page.getByRole('button', { name: '샘플 문서로 체험' })).toBeVisible();
});

test('a question gathers folders and the answer opens what it cites', async ({ page }) => {
  const fake = await fakeApi(page);
  await page.goto('/');
  await page.getByRole('textbox', { name: '문서에 대해 질문하세요' }).fill('시험 압력은?');
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\?q=/);
  await expect.poll(() => fake.asked).toEqual(['시험 압력은?']);

  const tree = page.getByRole('tree');
  // The found document opens on its linked one; that one's own folder starts closed (+1).
  await expect(tree.getByRole('treeitem')).toHaveCount(2);
  await expect(tree.getByText('인용됨')).toBeVisible();
  await expect(tree.getByText('+1')).toBeVisible();

  // A table the answer writes is a table, not its Markdown.
  await expect(page.getByRole('columnheader', { name: '기준' })).toBeVisible();
  await expect(page.getByText('|---|')).toHaveCount(0);

  // The answer cites the spec: it is selected in the folders and shown on the right.
  await page.getByRole('button', { name: 'SPEC-1_가상 규격서.docx' }).click();
  await expect(tree.getByRole('treeitem', { selected: true })).toContainText('SPEC-1_가상 규격서');
  await expect(page.getByRole('heading', { name: 'SPEC-1_가상 규격서' })).toBeVisible();
  await expect(page.getByLabel('미리보기').locator('blockquote')).toHaveText('시험 압력은 1.0 MPa이다.');

  // The keyboard walks the tree.
  await tree.getByRole('treeitem').first().focus();
  await page.keyboard.press('ArrowDown');
  await expect(tree.getByRole('treeitem').nth(1)).toBeFocused();
  await page.keyboard.press('ArrowRight');
  await expect(tree.getByRole('treeitem')).toHaveCount(3);
  await page.keyboard.press('ArrowRight');
  await expect(tree.getByRole('treeitem').nth(2)).toBeFocused();
  await page.keyboard.press('ArrowLeft');
  await expect(tree.getByRole('treeitem').nth(1)).toBeFocused();
  await page.keyboard.press('ArrowLeft');
  await page.keyboard.press('ArrowLeft');
  await expect(tree.getByRole('treeitem').first()).toBeFocused();
});

test('a reload shows what was found and answered without asking again', async ({ page }) => {
  const fake = await fakeApi(page);
  await page.goto(`/?q=${encodeURIComponent('시험 압력은?')}&k=5`);
  await expect(page.getByRole('button', { name: 'SPEC-1_가상 규격서.docx' })).toBeVisible();
  expect([fake.asked.length, fake.answered]).toEqual([1, 1]);
  await page.reload();
  await expect(page.getByRole('button', { name: 'SPEC-1_가상 규격서.docx' })).toBeVisible();
  await expect(page.getByRole('tree')).toBeVisible();
  expect([fake.asked.length, fake.answered]).toEqual([1, 1]);
});

test('the graph is reset only once the word is typed', async ({ page }) => {
  const fake = await fakeApi(page);
  await page.goto('/manage/danger');
  const reset = page.getByRole('button', { name: '그래프 초기화' });
  await expect(reset).toBeDisabled();
  await page.getByLabel(/확인하려면/).fill('초기화');
  await reset.click();
  await expect.poll(() => fake.reset).toBe(1);
});

test('the library lists the graph with each file’s date', async ({ page }) => {
  await fakeApi(page);
  await page.goto('/manage');
  await expect(page.getByRole('link', { name: /PROC-2_가상 절차서/ })).toBeVisible();
  await expect(page.getByText('그래프에 있음 · 3')).toBeVisible();
});

test('a search stopped says so and can be asked again', async ({ page }) => {
  await fakeApi(page, { slow: 3000 });
  await page.goto(`/?q=${encodeURIComponent('시험 압력은?')}&k=5`);
  await page.getByRole('button', { name: '중지' }).click();
  await expect(page.getByText('찾기를 멈췄습니다')).toBeVisible();
  await page.getByRole('button', { name: '다시 찾기' }).click();
  await expect(page.getByRole('tree')).toBeVisible({ timeout: 10_000 });
});

test('a passage count out of range is asked with the default', async ({ page }) => {
  const fake = await fakeApi(page);
  await page.goto(`/?q=${encodeURIComponent('시험 압력은?')}&k=999`);
  await expect(page.getByRole('tree')).toBeVisible();
  expect(fake.topK).toEqual([5]);
});

test('with no document chosen, nothing can be downloaded', async ({ page }) => {
  await fakeApi(page);
  await page.goto('/manage/export');
  await page.getByRole('button', { name: '모두 해제' }).click();
  await page.getByText('고급').click();
  for (const name of ['묶음 받기 (.zip)', 'Cypher', 'JSON']) {
    await expect(page.getByText(name, { exact: true })).not.toHaveAttribute('href', /.+/);
  }
  await expect(page.getByRole('button', { name: '.dump 만들기' })).toBeDisabled();
});

test('a key is put in on the settings page and never shown back', async ({ page }) => {
  const fake = await fakeApi(page);
  await page.goto('/');
  await expect(page.getByRole('alert')).toContainText('OPENAI_API_KEY가 비어 있습니다');
  await page.getByRole('link', { name: '설정 열기' }).click();
  await page.getByLabel('OpenAI API 키').fill('sk-test-abcd');
  await page.getByRole('button', { name: '저장' }).click();
  await expect(page.getByRole('status')).toHaveText('저장했습니다');
  expect(fake.saved).toEqual([{ OPENAI_API_KEY: 'sk-test-abcd' }]);
  await expect(page.getByText('설정됨 · …abcd')).toBeVisible();
  await expect(page.getByLabel('OpenAI API 키')).toHaveValue('');
});

test('choosing Anthropic asks for its key on the same save', async ({ page }) => {
  const fake = await fakeApi(page);
  await page.goto('/');
  await page.getByRole('link', { name: '설정 열기' }).click();
  await expect(page.getByLabel('Anthropic API 키')).toHaveCount(0);
  await page.getByLabel('채팅 제공자').selectOption('anthropic');
  await page.getByLabel('Anthropic API 키').fill('sk-ant-test-abcd');
  await page.getByRole('button', { name: '저장' }).click();
  await expect(page.getByRole('status')).toHaveText('저장했습니다');
  expect(fake.saved).toEqual([{ GRAPHME_PROVIDER: 'anthropic', ANTHROPIC_API_KEY: 'sk-ant-test-abcd' }]);
});

test('the samples are offered only by a server that has them', async ({ page }) => {
  await fakeApi(page, { empty: true, samples: false });
  await page.goto('/');
  await expect(page.getByText('문서 폴더를 끌어다 놓으세요')).toBeVisible();
  await expect(page.getByRole('button', { name: '폴더 선택' })).toBeVisible();
  await expect(page.getByRole('button', { name: '샘플 문서로 체험' })).toHaveCount(0);
});

const BUILD = { id: 'b1', kind: 'build', state: 'reading', done: 1, total: 3, source: '', failed: [], documents: 0,
  known: 0, carried: 0, links: 0, skipped: 0, embedded: 0, error: null };

test('the build under way is the newest build job, and its cancel names it', async ({ page }) => {
  const fake = await fakeApi(page, { jobs: [
    { id: 'd1', kind: 'dump', state: 'done', message: '4 KB', documents: 3, finished_at: 1767225600, dump_ready: true },
    BUILD,
    { ...BUILD, id: 'b0', state: 'done' },
  ] });
  await page.goto('/manage');
  await page.getByRole('button', { name: '취소' }).click();
  await expect.poll(() => fake.cancelled).toEqual(['b1']);
});

test('a finished dump is fetched from its own job', async ({ page }) => {
  await fakeApi(page, { jobs: [
    { id: 'd1', kind: 'dump', state: 'done', message: '4 KB', documents: 3, finished_at: 1767225600, dump_ready: true },
  ] });
  await page.goto('/manage/export');
  await expect(page.getByRole('link', { name: /neo4j\.dump 받기/ })).toHaveAttribute('href', '/v1/jobs/d1/file');
});

test('a server with a key asks for it, says when it is wrong, and is then asked with it', async ({ page }) => {
  const fake = await fakeApi(page, { key: 'k-invented' });
  await page.goto('/manage');
  const gate = page.getByRole('dialog', { name: 'API 키' });
  await expect(gate).toContainText('이 서버는 키를 요구합니다');
  await gate.getByLabel('서버 API 키').fill('k-wrong');
  await gate.getByRole('button', { name: '열기' }).click();
  await expect(gate).toContainText('키가 맞지 않습니다');
  await gate.getByLabel('서버 API 키').fill('k-invented');
  await gate.getByRole('button', { name: '열기' }).click();
  await expect(page.getByRole('link', { name: /PROC-2_가상 절차서/ })).toBeVisible();
  await expect(gate).toHaveCount(0);
  const cookie = (await page.context().cookies()).find((c) => c.name === 'graphme_key');
  expect([cookie?.value, cookie?.sameSite, cookie?.expires]).toEqual(['k-invented', 'Strict', -1]);
  expect(fake.refused).toBeGreaterThan(0);
});

test('the files a question found are picked by file, by hop or all, and downloaded in one zip', async ({ page }) => {
  await fakeApi(page);
  await page.goto('/?q=%EC%8B%9C%ED%97%98%20%EC%95%95%EB%A0%A5%EC%9D%80%3F');
  await page.getByRole('button', { name: '받기' }).click();
  const dialog = page.getByRole('dialog');
  const link = dialog.getByRole('link', { name: /선택한 \d+개 받기/ });
  const docsIn = async () => new URL((await link.getAttribute('href'))!, 'http://x').searchParams.getAll('doc');

  // All picked to begin with, each in its hop's folder.
  await expect(link).toHaveText('선택한 3개 받기');
  expect(await docsIn()).toEqual(['proc', 'spec', 'deck']);
  const folders = new URL((await link.getAttribute('href'))!, 'http://x').searchParams.getAll('folder');
  expect(folders).toEqual(['1차 - 질문으로 찾은 문서', '2차 - 연결된 문서', '3차 - 더 연결된 문서']);

  // A hop at a time.
  await dialog.getByRole('checkbox', { name: '2차 - 연결된 문서' }).uncheck();
  expect(await docsIn()).toEqual(['proc', 'deck']);
  // One file.
  await dialog.getByRole('checkbox', { name: '가상 보고.pptx' }).uncheck();
  expect(await docsIn()).toEqual(['proc']);
  // All, off and on.
  await dialog.getByRole('button', { name: '모두 해제' }).click();
  await expect(dialog.getByRole('button', { name: '선택한 0개 받기' })).toBeDisabled();
  await dialog.getByRole('button', { name: '모두 선택' }).click();
  expect(await docsIn()).toEqual(['proc', 'spec', 'deck']);
});

test.describe('on a Korean browser', () => {
  test.use({ locale: 'ko-KR' });

  test('the app speaks English until Korean is chosen', async ({ page }) => {
    await page.addInitScript(() => localStorage.removeItem('lang'));
    await fakeApi(page, { empty: true });
    await page.goto('/');
    await expect(page.getByText('Drop a folder of documents')).toBeVisible();
  });
});

test('Google Drive is connected in the browser, then its picked files are built', async ({ page }) => {
  const fake = await fakeApi(page);
  await page.goto('/manage/drive');
  await expect(page.getByText('연결되지 않았습니다.')).toBeVisible();

  await page.getByRole('button', { name: '연결하고 파일 고르기' }).click();
  await expect(page.getByRole('link', { name: '이 링크를 여세요' })).toHaveAttribute('href', /trigger_onepick/);
  await expect(page.getByText('연결됨 · 2개 골랐습니다')).toBeVisible();

  await page.getByRole('button', { name: 'Drive에서 그래프 만들기' }).click();
  await expect.poll(() => fake.drive.built).toBe(1);
  await page.getByRole('button', { name: '연결 해제' }).click();
  await expect(page.getByText('연결되지 않았습니다.')).toBeVisible();
});

test('a file read and waiting for its turn in the build is not shown as left out', async ({ page }) => {
  await fakeApi(page, {
    jobs: [{ id: 'b', kind: 'build', state: 'reading', done: 1, total: 2, source: 'Google Drive', failed: [] }],
    reading: [{ name: '가상 점검표.pdf', format: 'pdf', in_space: false, document_id: null, file_id: 'f1',
      state: 'queued', modified: 1767225600 }],
  });
  await page.goto('/manage');
  const row = page.getByRole('link', { name: /가상 점검표/ }).locator('..');
  await expect(row.getByText('추가 대기')).toBeVisible();
  await expect(row.getByText('그래프 밖', { exact: true })).toHaveCount(0);
  await expect(row.getByRole('button', { name: '다시 읽기' })).toHaveCount(0); // nothing to redo
});

test('while a build runs the Drive page says so and does not start another', async ({ page }) => {
  const fake = await fakeApi(page, {
    jobs: [{ id: 'b', kind: 'build', state: 'reading', done: 1, total: 2, source: 'Google Drive', failed: [] }],
  });
  fake.drive.connected = true;
  fake.drive.picked = 2;
  await page.goto('/manage/drive');
  await expect(page.getByRole('button', { name: 'Drive에서 그래프 만들기' })).toBeDisabled();
  await expect(page.getByText('고른 파일을 읽고 있습니다', { exact: false })).toBeVisible();
});
