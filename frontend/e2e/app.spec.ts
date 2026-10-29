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

test('a citation takes the preview back to its passage, however far the reader moved', async ({ page }) => {
  await fakeApi(page);
  await page.goto(`/?q=${encodeURIComponent('시험 압력은?')}&k=5`);
  const cite = page.getByRole('button', { name: 'SPEC-1_가상 규격서.docx' });
  await cite.click();
  const tabs = page.getByLabel('미리보기').getByRole('tab');
  // The spec's passage is on its second page.
  await expect(tabs.nth(1)).toHaveAttribute('aria-selected', 'true');
  await tabs.nth(0).click();
  await expect(tabs.nth(0)).toHaveAttribute('aria-selected', 'true');
  await cite.click();
  await expect(tabs.nth(1)).toHaveAttribute('aria-selected', 'true');
});

test('a passage count changed on a results page searches and answers again with it', async ({ page }) => {
  const fake = await fakeApi(page);
  await page.goto(`/?q=${encodeURIComponent('시험 압력은?')}&k=5`);
  await expect(page.getByRole('tree')).toBeVisible();
  await page.getByRole('combobox').selectOption('10');
  await expect(page).toHaveURL(/k=10/);
  await expect.poll(() => fake.topK).toEqual([5, 10]);
  await expect.poll(() => fake.answered).toBe(2);
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
  await page.getByLabel(/확인하려면 ‘초기화’/).fill('초기화');
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
  await expect(page.getByRole('alert')).toContainText('The LLM API key is empty');
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
  // Who answers is an advanced setting; chosen there, its key appears beside the others.
  await expect(page.getByLabel('채팅 제공자')).toBeHidden();
  await page.getByText('고급 설정').click();
  await page.getByLabel('채팅 제공자').selectOption('anthropic');
  await expect(page.getByText('채팅용', { exact: true })).toBeVisible();
  await page.getByLabel('Anthropic API 키').fill('sk-ant-test-abcd');
  await page.getByRole('button', { name: '저장' }).click();
  await expect(page.getByRole('status')).toHaveText('저장했습니다');
  expect(fake.saved).toEqual([{ GRAPHME_PROVIDER: 'anthropic', ANTHROPIC_API_KEY: 'sk-ant-test-abcd' }]);
});

test('the search model is chosen from two, each saying whether it is installed', async ({ page }) => {
  const fake = await fakeApi(page);
  await page.goto('/manage/settings');
  const general = page.getByRole('radio', { name: /공용/ });
  const korean = page.getByRole('radio', { name: /한국어/ });
  await expect(general).toBeChecked();
  await expect(page.getByTestId('search-model-general')).toContainText('설치 중 50%');
  await expect(page.getByTestId('search-model-korean')).toContainText('설치됨');
  // The one not in use can be removed, then installed again.
  await page.getByTestId('search-model-korean').getByRole('button', { name: '삭제' }).click();
  await expect(page.getByTestId('search-model-korean')).toContainText('설치 안 됨');
  await page.getByTestId('search-model-korean').getByRole('button', { name: '설치' }).click();
  expect(fake.removed).toEqual(['korean']);
  expect(fake.installed).toEqual(['korean']);
  // Choosing one is saved at once; the one in use is never offered for removal.
  await korean.check();
  await expect.poll(() => fake.saved).toEqual([{ GRAPHME_EMBEDDING_PROVIDER: 'korean' }]);
  await expect(korean).toBeChecked();
  await expect(page.getByTestId('search-model-korean').getByRole('button', { name: '삭제' })).toHaveCount(0);
});

test('Box folders are browsed, picked and read into the graph', async ({ page }) => {
  const fake = await fakeApi(page);
  await page.goto('/manage/box');
  await expect(page.getByRole('heading', { name: 'Box', exact: true })).toBeVisible();
  await page.getByTestId('box-api').getByRole('checkbox', { name: 'Specs' }).check();
  await expect.poll(() => fake.box.picked).toEqual([{ type: 'folder', id: '10', name: 'Specs' }]);
  // A folder opens a level down; the way back is the path above the list.
  await page.getByTestId('box-api').getByRole('row', { name: /Specs/ }).dblclick();
  await expect(page.getByText('Valve Spec.docx')).toBeVisible();
  await page.getByRole('button', { name: 'All Files' }).click();
  await expect(page.getByText('Leak Test Procedure.pdf')).toBeVisible();
  await expect(page.getByText('1개 골랐습니다')).toBeVisible();
  await page.getByRole('button', { name: 'Box에서 그래프 만들기' }).click();
  await expect.poll(() => fake.box.built).toBe(1);
});

test('Box Drive is read only once the user connects it, and then on every start', async ({ page }) => {
  const fake = await fakeApi(page, { box: false });
  await page.goto('/manage/box');
  const drive = page.getByTestId('box-drive');
  await expect(drive.getByText('/Users/me/Library/CloudStorage/Box-Box')).toBeVisible();
  await expect(drive.getByRole('checkbox', { name: 'Plan.pdf' })).toHaveCount(0);  // nothing listed yet
  await drive.getByRole('button', { name: '연결', exact: true }).click();
  await expect(drive.getByRole('checkbox', { name: 'Plan.pdf' })).toBeVisible();
  await page.reload();
  await expect(page.getByTestId('box-drive').getByRole('checkbox', { name: 'Plan.pdf' })).toBeVisible();
  expect(fake.connected).toEqual({ box: true, google: false, onedrive: false });
});

test('Box Drive is browsed like a folder: double-click opens, only documents can be checked', async ({ page }) => {
  await fakeApi(page, { box: false });
  await page.goto('/manage/box');
  const drive = page.getByTestId('box-drive');
  await drive.getByRole('button', { name: '연결', exact: true }).click();
  await expect(drive.getByRole('checkbox', { name: 'notes.txt' })).toBeDisabled();
  await expect(drive.getByRole('row', { name: /Plan\.pdf/ })).toContainText('2.4 MB');
  await drive.getByRole('row', { name: /Quality/ }).dblclick();
  await expect(drive.getByText('Audit.docx')).toBeVisible();
  // Going back is always the first row of the listing, with Select all right under it.
  const up = drive.getByRole('table').getByRole('button', { name: '뒤로' });
  const all = drive.getByRole('checkbox', { name: '모두 선택' });
  expect((await up.boundingBox())!.y).toBeLessThan((await all.boundingBox())!.y);
  await up.click();
  await expect(drive.getByText('Plan.pdf')).toBeVisible();
  await expect(drive.getByRole('button', { name: '뒤로' })).toBeDisabled();  // at the top already
});

test('Box Drive on this machine is found, browsed and built from', async ({ page }) => {
  const fake = await fakeApi(page, { box: false });
  await page.goto('/manage/box');
  const drive = page.getByTestId('box-drive');
  await drive.getByRole('button', { name: '연결', exact: true }).click();
  await drive.getByRole('row', { name: /Quality/ }).dblclick();
  await drive.getByRole('checkbox', { name: 'Audit.docx' }).check();
  await drive.getByRole('button', { name: 'Box-Box' }).click();
  await drive.getByRole('checkbox', { name: 'Plan.pdf' }).check();
  await drive.getByRole('button', { name: 'Box Drive에서 그래프 만들기' }).click();
  await expect.poll(() => fake.boxDrive).toEqual([[
    '/Users/me/Library/CloudStorage/Box-Box/Quality/Audit.docx',
    '/Users/me/Library/CloudStorage/Box-Box/Plan.pdf']]);
});

test('everything in a Box Drive folder is checked at once', async ({ page }) => {
  const fake = await fakeApi(page, { box: false });
  await page.goto('/manage/box');
  const drive = page.getByTestId('box-drive');
  await drive.getByRole('button', { name: '연결', exact: true }).click();
  const all = drive.getByRole('checkbox', { name: '모두 선택' });
  await all.check();
  await expect(drive.getByRole('checkbox', { name: 'Quality' })).toBeChecked();
  await expect(drive.getByRole('checkbox', { name: 'Plan.pdf' })).toBeChecked();
  await drive.getByRole('checkbox', { name: 'Plan.pdf' }).uncheck();
  await expect(all).not.toBeChecked();
  await all.check();
  await drive.getByRole('button', { name: 'Box Drive에서 그래프 만들기' }).click();
  await expect.poll(() => fake.boxDrive).toEqual([[
    '/Users/me/Library/CloudStorage/Box-Box/Quality', '/Users/me/Library/CloudStorage/Box-Box/Plan.pdf']]);
});

test('everything in a Box folder is picked at once', async ({ page }) => {
  const fake = await fakeApi(page);
  await page.goto('/manage/box');
  const listed = page.getByTestId('box-api');
  await listed.getByRole('checkbox', { name: '모두 선택' }).check();
  await expect.poll(() => fake.box.picked).toEqual([
    { type: 'folder', id: '10', name: 'Specs' }, { type: 'file', id: 'f1', name: 'Leak Test Procedure.pdf' }]);
});

test('Box without an app says how to make one, with the address to register', async ({ page }) => {
  await fakeApi(page, { box: false });
  await page.goto('/manage/box');
  await expect(page.getByText('/v1/cloud/box/callback')).toBeVisible();
  await expect(page.locator('p').getByRole('link', { name: '키와 모델' })).toBeVisible();
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

test('a finished build says how many went in and why each failure failed, beside its name', async ({ page }) => {
  await fakeApi(page, { jobs: [{ ...BUILD, state: 'done', done: 3, total: 3, documents: 3, known: 1, elapsed: 125,
    failed: ['Sealed Budget.xlsx: the file is locked by a password or rights protection -- open it in Office, remove the protection, and add it again',
      'Archive: the folder did not list in time -- not read'] }] });
  await page.goto('/manage');
  await expect(page.getByText('성공 3 · 실패 2')).toBeVisible();
  await page.getByRole('button', { name: '결과 보기' }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText('이미 있음 1')).toBeVisible();
  const row = dialog.getByRole('row', { name: /Sealed Budget\.xlsx/ });
  await expect(row.getByRole('cell').nth(0)).toHaveText('Sealed Budget.xlsx');
  await expect(row.getByRole('cell').nth(1)).toHaveText('암호나 권한으로 보호된 파일입니다 — Office에서 보호를 풀고 다시 넣어 주세요');
  await expect(dialog.getByRole('row', { name: /Archive/ }).getByRole('cell').nth(1)).toHaveText('폴더 목록을 제때 받지 못해 읽지 않았습니다');
});

test('a build under way shows no result of the build before it', async ({ page }) => {
  await fakeApi(page, { jobs: [BUILD] });
  await page.goto('/manage');
  await expect(page.getByRole('button', { name: '취소' })).toBeVisible();
  await expect(page.getByRole('button', { name: '결과 보기' })).toHaveCount(0);
});

test('a build under way says how far its reading is', async ({ page }) => {
  await fakeApi(page, { jobs: [{ ...BUILD, percent: 42 }] });
  await page.goto('/manage');
  await expect(page.getByText('그래프 만드는 중 42%')).toBeVisible();
});

test('a build looking through folders says how many documents it has found', async ({ page }) => {
  await fakeApi(page, { jobs: [{ ...BUILD, done: 0, total: 0, looked: 120 }] });
  await page.goto('/manage');
  await expect(page.getByText('폴더를 살펴보는 중 · 문서 120개 찾음').first()).toBeVisible();
});

test('a document being read says how far, and which stage', async ({ page }) => {
  await fakeApi(page, { progress: { stage: 'outline', done: 12, total: 40, percent: 63 } });
  await page.goto('/manage/docs/f1?demo=1');
  await expect(page.getByText('읽는 중 · 63% — 개요 12/40')).toBeVisible();
});

test('a build and a reading say how long they have taken', async ({ page }) => {
  await fakeApi(page, { jobs: [{ ...BUILD, percent: 42, elapsed: 125 }],
    progress: { stage: 'outline', done: 12, total: 40, percent: 63 }, elapsed: 65 });
  await page.goto('/manage/docs/f1?demo=1');
  await expect(page.getByText('그래프 만드는 중 42% · 2분 5초')).toBeVisible();
  await expect(page.getByText('읽는 중 · 63% — 개요 12/40 · 1분 5초')).toBeVisible();
});

test('a document read says how long its reading took', async ({ page }) => {
  await fakeApi(page, { elapsed: 3725 });
  await page.goto('/manage/docs/f1?demo=1');
  await expect(page.getByText('읽기 완료 · 1시간 2분 5초')).toBeVisible();
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

test('Google Drive for desktop is connected once, then browsed and built from', async ({ page }) => {
  const fake = await fakeApi(page);
  await page.goto('/manage/drive');
  const drive = page.getByTestId('google-drive');
  await expect(drive.getByText('/Users/me/Library/CloudStorage/GoogleDrive-me@invented.test')).toBeVisible();
  await drive.getByRole('button', { name: '연결', exact: true }).click();
  await drive.getByRole('checkbox', { name: 'Plan.pdf' }).check();
  await drive.getByRole('button', { name: 'Google Drive에서 그래프 만들기' }).click();
  await expect.poll(() => fake.boxDrive).toEqual([['/Users/me/Library/CloudStorage/GoogleDrive-me@invented.test/Plan.pdf']]);
  expect(fake.connected).toEqual({ box: false, google: true, onedrive: false });
  await drive.getByRole('button', { name: '연결 해제' }).click();
  await expect(drive.getByRole('button', { name: '연결', exact: true })).toBeVisible();
});

test('without the drive\'s desktop app the page links to its install page, then finds it', async ({ page }) => {
  const fake = await fakeApi(page, { googleDrive: false });
  await page.goto('/manage/drive');
  const drive = page.getByTestId('google-drive');
  await expect(drive.getByRole('link', { name: 'Google Drive 설치하기' }))
    .toHaveAttribute('href', 'https://www.google.com/drive/download/');
  fake.found.google = true;  // installed meanwhile
  await drive.getByRole('button', { name: '설치했으면 다시 확인' }).click();
  await expect(drive.getByRole('button', { name: '연결', exact: true })).toBeVisible();
});

test('without Box Drive the Box page links to Box\'s downloads', async ({ page }) => {
  await fakeApi(page, { boxDrive: false });
  await page.goto('/manage/box');
  await expect(page.getByTestId('box-drive').getByRole('link', { name: 'Box Drive 설치하기' }))
    .toHaveAttribute('href', 'https://www.box.com/resources/downloads');
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
  fake.connected.google = true;
  await page.goto('/manage/drive');
  await expect(page.getByRole('button', { name: 'Google Drive에서 그래프 만들기' })).toBeDisabled();
  await expect(page.getByText('그래프를 만드는 중입니다', { exact: false })).toBeVisible();
});

test('the workspace open is named in the top bar; another is opened, made and renamed there', async ({ page }) => {
  const fake = await fakeApi(page);
  await page.goto('/manage');
  const menu = page.getByRole('button', { name: /워크스페이스: Default/ });
  await menu.click();
  await page.getByRole('menuitemradio', { name: 'Invented audits' }).click();
  await expect(page.getByRole('button', { name: /워크스페이스: Invented audits/ })).toBeVisible();
  expect(fake.opened).toEqual(['w2']);

  await page.getByRole('button', { name: /워크스페이스: Invented audits/ }).click();
  await page.getByRole('menuitem', { name: '새 워크스페이스' }).click();
  await page.getByRole('dialog').getByRole('textbox').fill('Invented project');
  await page.getByRole('dialog').getByRole('button', { name: '만들기' }).click();
  await expect(page.getByRole('button', { name: /워크스페이스: Invented project/ })).toBeVisible();
  expect(fake.opened).toEqual(['w2', 'w3']);  // made, then opened

  await page.getByRole('button', { name: /워크스페이스: Invented project/ }).click();
  await page.getByRole('menuitem', { name: '이름 바꾸기' }).click();
  await expect(page.getByRole('dialog').getByRole('textbox')).toHaveValue('Invented project');
  await page.getByRole('dialog').getByRole('textbox').fill('Invented pilot');
  await page.getByRole('dialog').getByRole('button', { name: '저장' }).click();
  await expect(page.getByRole('button', { name: /워크스페이스: Invented pilot/ })).toBeVisible();
});

test('a workspace is deleted only once its name is typed, and never the one open', async ({ page }) => {
  const fake = await fakeApi(page);
  await page.goto('/manage/danger');
  const section = page.getByTestId('delete-workspace');
  await expect(section.getByRole('combobox')).not.toContainText('Default');  // the one open
  const remove = section.getByRole('button', { name: '워크스페이스 삭제' });
  await expect(remove).toBeDisabled();
  await section.getByRole('textbox').fill('Invented audit');
  await expect(remove).toBeDisabled();
  await section.getByRole('textbox').fill('Invented audits');
  await remove.click();
  await expect.poll(() => fake.deletedWorkspaces).toEqual(['w2']);
});

test('a server with its own graph DB shows no workspaces', async ({ page }) => {
  await fakeApi(page, { workspaces: false });
  await page.goto('/manage/danger');
  await expect(page.getByRole('button', { name: /워크스페이스:/ })).toHaveCount(0);
  await expect(page.getByTestId('delete-workspace')).toHaveCount(0);
});

test('OneDrive and its SharePoint libraries are connected once, browsed and built from', async ({ page }) => {
  const fake = await fakeApi(page);
  await page.goto('/manage');
  await page.getByRole('link', { name: 'OneDrive/SharePoint' }).click();
  await expect(page.getByText('SharePoint 라이브러리는', { exact: false })).toBeVisible();  // how to sync one
  const drive = page.getByTestId('onedrive-drive');
  await drive.getByRole('button', { name: '연결', exact: true }).click();
  await drive.getByRole('checkbox', { name: 'Plan.pdf' }).check();
  await drive.getByRole('button', { name: 'OneDrive에서 그래프 만들기' }).click();
  await expect.poll(() => fake.boxDrive).toEqual([['/Users/me/Library/CloudStorage/CloudStorage/Plan.pdf']]);
  expect(fake.connected.onedrive).toBe(true);
});

test('without OneDrive the page links to its download', async ({ page }) => {
  await fakeApi(page, { oneDrive: false });
  await page.goto('/manage/onedrive');
  await expect(page.getByTestId('onedrive-drive').getByRole('link', { name: 'OneDrive 설치하기' }))
    .toHaveAttribute('href', 'https://www.microsoft.com/microsoft-365/onedrive/download');
});

test('a connected drive says how much of it is kept on this computer', async ({ page }) => {
  await fakeApi(page);
  await page.goto('/manage/box');
  const drive = page.getByTestId('box-drive');
  await drive.getByRole('button', { name: '연결', exact: true }).click();
  await expect(drive.getByText('이 컴퓨터에서 차지하는 용량 · 991.9 MB (파일 102개)')).toBeVisible();
  await expect(drive.getByText('빌드가 읽은 온라인 전용 파일은', { exact: false })).toBeVisible();
});
