import { chromium } from 'playwright';
import { copyFile, mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const project = path.resolve(here, '..');
const screenshots = path.resolve(project, '../docs/screenshots');
const work = path.resolve(project, '../../../work/datasetguard-verification');
const base = process.env.DATASETGUARD_FRONTEND_URL || 'http://127.0.0.1:5173/';
const api = process.env.DATASETGUARD_API_URL || 'http://127.0.0.1:8000';
const chromePath = process.env.DATASETGUARD_CHROME || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
await mkdir(screenshots, { recursive: true });
await mkdir(work, { recursive: true });

const browser = await chromium.launch({ executablePath: chromePath, headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
const page = await context.newPage();
const consoleErrors = [];
const pageErrors = [];
const failedRequests = [];
const badResponses = [];
const expectedErrors = [];
let expectingBadPassword = false;
page.on('console', msg => {
  if (msg.type() !== 'error') return;
  const text = msg.text();
  if (expectingBadPassword && /401|Unauthorized/i.test(text)) expectedErrors.push({ kind: 'console', message: text });
  else consoleErrors.push(text);
});
page.on('pageerror', error => pageErrors.push(error.message));
page.on('requestfailed', req => failedRequests.push({ url: req.url(), failure: req.failure()?.errorText }));
page.on('response', res => {
  if (res.status() < 400) return;
  const record = { status: res.status(), url: res.url() };
  if (expectingBadPassword && res.status() === 401 && res.url().endsWith('/api/auth/login')) expectedErrors.push({ kind: 'response', ...record });
  else badResponses.push(record);
});
const shots = [];
const outputs = {};
const shot = async name => { const file = path.join(screenshots, `${name}.png`); await page.screenshot({ path: file, fullPage: true, animations: 'disabled' }); shots.push(path.basename(file)); };
const step = async (name, fn) => { await fn(); console.log(`PASS ${name}`); };
const assertVisible = async locator => { if (!(await locator.isVisible())) throw new Error(`Expected visible: ${locator}`); };

try {
  await page.goto(base, { waitUntil: 'networkidle' });
  await assertVisible(page.getByRole('heading', { name: 'Welcome back' }));
  await shot('01-registration-login');

  const email = `dg-browser-${Date.now()}@example.test`;
  const password = 'Local-Browser-QA-92741!';
  await step('registration form', async () => {
    await page.getByRole('button', { name: 'Create account', exact: true }).click();
    await page.getByLabel('Email address').fill(email);
    await page.getByLabel('Password').fill(password);
    await page.getByRole('button', { name: 'Create account', exact: false }).click();
    await page.getByRole('heading', { name: 'Dataset overview' }).waitFor();
  });
  await assertVisible(page.getByRole('heading', { name: 'Start with a dataset' }));
  await shot('02-dataset-upload');

  await step('sample dataset load and profile', async () => {
    await page.getByRole('button', { name: 'Customer quality' }).click();
    await page.getByText('81', { exact: true }).first().waitFor({ state: 'visible' }).catch(() => {});
    await page.getByText('customer_quality.csv', { exact: false }).first().waitFor();
  });
  await step('analysis and populated dashboard', async () => {
    await page.getByRole('button', { name: /Run full analysis/ }).click();
    await page.getByText(/Analysis complete/).waitFor();
    await page.locator('.recharts-wrapper').first().waitFor();
    await page.locator('.recharts-wrapper').nth(1).waitFor();
  });
  await shot('03-main-dashboard');
  await step('dataset explorer profile', async () => {
    await page.getByRole('button', { name: 'Data explorer' }).click();
    await page.getByRole('columnheader', { name: /customer_code/ }).waitFor();
    await page.locator('table tbody tr').first().waitFor();
  });
  await shot('04-dataset-profiling');

  await step('analysis findings view', async () => {
    await page.getByRole('button', { name: /Findings/ }).click();
    await page.locator('.finding-row.full').first().waitFor();
  });
  await shot('05-detection-findings');
  await step('finding details and evidence', async () => {
    await page.locator('.finding-row.full').first().click();
    await page.getByText('SUPPORTING EVIDENCE').waitFor();
    await page.locator('.finding-drawer pre').waitFor();
  });
  await shot('06-problem-details');
  await page.locator('.finding-drawer .icon-btn').click();

  await step('cleaning operation', async () => {
    await page.getByRole('button', { name: 'Cleaning studio' }).click();
    await page.getByRole('heading', { name: 'Make careful, reversible changes' }).waitFor();
    await page.getByRole('button', { name: /Impute missing values/ }).click();
    await page.getByText(/Cleaning applied/).waitFor();
  });
  await shot('07-cleaning-studio');

  const customerFile = path.resolve(project, '../sample_datasets/customer_quality.csv');
  const originalFile = path.join(work, 'customer_quality_original.csv');
  await copyFile(customerFile, originalFile);
  await step('file upload', async () => {
    await page.locator('.heading-actions input[type=file]').setInputFiles(originalFile);
    await page.getByText('Dataset uploaded and profiled.').waitFor();
  });
  await step('dataset comparison', async () => {
    await page.getByRole('button', { name: 'Compare datasets' }).click();
    await page.getByRole('button', { name: 'Compare distributions' }).click();
    await page.getByRole('heading', { name: /customer_quality/ }).waitFor();
    await page.locator('.compare-table tbody tr').first().waitFor();
    const summary = await page.locator('.compare-metrics').innerText();
    if (!summary.includes('77')) throw new Error(`Expected 77 exact shared rows after imputation; received: ${summary}`);
    outputs.comparisonSummary = summary;
  });
  await shot('08-dataset-comparison');

  await step('ML baseline evaluation', async () => {
    await page.getByRole('button', { name: 'Overview' }).click();
    const targetSelect = page.locator('.select-label select');
    await targetSelect.selectOption({ label: 'segment' });
    await page.getByRole('button', { name: /Run full analysis/ }).click();
    await page.getByText(/Analysis complete/).waitFor();
    await page.getByRole('button', { name: 'Model impact' }).click();
    await page.locator('.form-select select').selectOption({ label: 'segment' });
    await page.getByRole('button', { name: /Run baseline experiment/ }).click();
    await page.getByText('F1 MACRO').waitFor();
  });
  await shot('09-model-impact');

  await step('CSV and PDF exports', async () => {
    await page.getByRole('button', { name: 'Reports' }).click();
    const csvPromise = page.waitForEvent('download');
    await page.getByRole('button', { name: /Download CSV/ }).click();
    const csv = await csvPromise;
    const csvPath = path.join(work, 'browser-cleaned-export.csv');
    await csv.saveAs(csvPath);
    const csvText = await readFile(csvPath, 'utf8');
    if (!csvText.startsWith('record_id,customer_code')) throw new Error('CSV download did not contain the expected dataset header.');
    const pdfPromise = page.waitForEvent('download');
    await page.getByRole('button', { name: /Generate PDF/ }).click();
    const pdf = await pdfPromise;
    const pdfPath = path.join(work, 'browser-audit-report.pdf');
    await pdf.saveAs(pdfPath);
    const pdfBytes = await readFile(pdfPath);
    if (pdfBytes.subarray(0,4).toString() !== '%PDF') throw new Error('PDF download is not a valid PDF file.');
    outputs.csvBytes = Buffer.byteLength(csvText);
    outputs.pdfBytes = pdfBytes.length;
  });
  await shot('10-reports');

  await step('logout and login', async () => {
    await page.getByTitle('Sign out').click();
    await page.getByRole('heading', { name: 'Welcome back' }).waitFor();
    await page.getByLabel('Email address').fill(email);
    await page.getByLabel('Password').fill('wrong-password-123');
    expectingBadPassword = true;
    await page.getByRole('button', { name: 'Sign in' }).click();
    await page.locator('.form-error').waitFor();
    expectingBadPassword = false;
    await page.getByLabel('Password').fill(password);
    await page.getByRole('button', { name: 'Sign in' }).click();
    await page.getByRole('heading', { name: 'Dataset overview' }).waitFor();
    const selectedDataset = await page.locator('.dataset-picker select option:checked').innerText();
    if (!selectedDataset.includes('customer_quality')) throw new Error(`Dataset history did not restore: ${selectedDataset}`);
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('.mobile-menu').click();
  await page.locator('.sidebar.show').waitFor();
  await page.getByRole('button', { name: 'Data explorer' }).click();
  await page.getByRole('heading', { name: 'Data explorer' }).waitFor();
  await page.evaluate(() => window.scrollTo({ left: 0, top: 0 }));
  await page.waitForTimeout(100);
  const { documentWidth, offenders, metrics } = await page.evaluate(() => ({
    documentWidth: document.documentElement.scrollWidth,
    metrics: ['body','.app-shell','.main','.content','.view-stack','.table-card','.table-toolbar','.table-scroll','.profile-grid'].map(s => { const e=document.querySelector(s), r=e?.getBoundingClientRect(); return [s,r&&Math.round(r.left),r&&Math.round(r.right),e?.scrollWidth,e?.clientWidth,getComputedStyle(e||document.body).overflowX]; }).concat([scrollX,document.documentElement.clientWidth]),
    offenders: [...document.querySelectorAll('body *')].map(el => {
      const r = el.getBoundingClientRect();
      return { tag: el.tagName, className: typeof el.className === 'string' ? el.className : '', right: Math.round(r.right), width: Math.round(r.width), text: (el.textContent || '').trim().slice(0, 45) };
    }).filter((item, i, all) => item.right > innerWidth + 1 && !document.querySelectorAll('body *')[i].closest('.table-scroll')).slice(0, 20)
  }));
  if (documentWidth > 390) throw new Error(`Mobile page overflows horizontally (${documentWidth}px > 390px): ${JSON.stringify({offenders,metrics})}`);
  outputs.mobileDocumentWidth = documentWidth;
  await page.screenshot({ path: path.join(screenshots, '11-mobile-layout.png'), fullPage: true, animations: 'disabled' });
  shots.push('11-mobile-layout.png');

  const result = {
    browser: 'Google Chrome via Playwright (fresh headless context)',
    frontend: base,
    account: email,
    passedSteps: ['registration', 'sample load/profile', 'data explorer', 'analysis/charts', 'finding evidence', 'cleaning', 'file upload', 'dataset comparison', 'ML baseline', 'CSV/PDF downloads', 'logout/login'],
    screenshots: shots,
    consoleErrors,
    pageErrors,
    failedRequests,
    badResponses,
    expectedErrors,
    outputs,
  };
  await writeFile(path.join(screenshots, 'browser-qa-results.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result, null, 2));
  if (consoleErrors.length || pageErrors.length || failedRequests.length || badResponses.length) process.exitCode = 1;
} finally {
  await context.close();
  await browser.close();
}
