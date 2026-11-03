import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { after, before, test } from 'node:test';

const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const resumeBytes = Buffer.from('%PDF-1.7\nfixture');
const resume = { name: 'matched-resume.pdf', mime: 'application/pdf', base64: resumeBytes.toString('base64'),
  sha256: createHash('sha256').update(resumeBytes).digest('hex') };
let browser, profile, base, rootConnection, script;
class Cdp {
  constructor(socket) {
    this.socket = socket;
    this.id = 0;
    this.pending = new Map();
    this.listeners = new Map();
    socket.addEventListener('message', ({ data }) => {
      const message = JSON.parse(data);
      if (message.id) {
        const pending = this.pending.get(message.id);
        if (!pending) return;
        this.pending.delete(message.id);
        clearTimeout(pending.timer);
        message.error ? pending.reject(new Error(JSON.stringify(message.error))) : pending.resolve(message.result);
      } else for (const listener of this.listeners.get(message.method) || []) listener(message.params);
    });
  }
  static async connect(url) {
    const socket = new WebSocket(url);
    await new Promise((resolve, reject) => {
      socket.addEventListener('open', resolve, { once: true });
      socket.addEventListener('error', reject, { once: true });
    });
    return new Cdp(socket);
  }
  on(name, listener) { this.listeners.set(name, [...(this.listeners.get(name) || []), listener]); }
  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error(`Timed out ${method}`)); }, 25_000);
      this.pending.set(id, { resolve, reject, timer });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }
  close() { this.socket.close(); }
}
before(async () => {
  script = await readFile(new URL('../extension/application-runner.js', import.meta.url), 'utf8');
  profile = await mkdtemp(path.join(tmpdir(), 'kula-forms-'));
  browser = spawn(process.env.EDGE_PATH || 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe', [
    '--headless=new', '--disable-gpu', '--no-first-run', '--disable-background-networking',
    '--disable-component-update', '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank',
  ], { stdio: ['ignore', 'pipe', 'pipe'] });
  let diagnostics = '';
  browser.stderr.on('data', (data) => { diagnostics += data; });
  const deadline = Date.now() + 20_000;
  while (Date.now() < deadline) {
    try {
      const [port, endpoint] = (await readFile(path.join(profile, 'DevToolsActivePort'), 'utf8')).trim().split(/\r?\n/);
      base = `http://127.0.0.1:${port}`;
      rootConnection = await Cdp.connect(`ws://127.0.0.1:${port}${endpoint}`);
      return;
    } catch { await pause(100); }
  }
  throw new Error(`Fixture browser failed to start: ${diagnostics}`);
});
after(async () => {
  if (rootConnection) {
    await rootConnection.send('Browser.close').catch(() => {});
    rootConnection.close();
  }
  if (browser?.exitCode === null) {
    await Promise.race([new Promise((resolve) => browser.once('exit', resolve)), pause(2000)]);
    if (browser.exitCode === null) browser.kill();
  }
  if (profile) await rm(profile, { recursive: true, force: true, maxRetries: 5 });
});

async function page(t, html, url = 'https://jobright.ai/jobs/info/fixture') {
  const target = await (await fetch(`${base}/json/new?about:blank`, { method: 'PUT' })).json();
  const cdp = await Cdp.connect(target.webSocketDebuggerUrl);
  t.after(async () => { cdp.close(); await rootConnection.send('Target.closeTarget', { targetId: target.id }).catch(() => {}); });
  await cdp.send('Page.enable');
  await cdp.send('Fetch.enable', { patterns: [{ urlPattern: `${new URL(url).origin}/*` }] });
  cdp.on('Fetch.requestPaused', ({ requestId, resourceType }) => {
    void cdp.send('Fetch.fulfillRequest', { requestId, responseCode: 200,
      responseHeaders: [{ name: 'Content-Type', value: 'text/html; charset=utf-8' }],
      body: Buffer.from(resourceType === 'Document' ? `<!doctype html><body>${html}</body>` : '').toString('base64'),
    }).catch(() => {});
  });
  await cdp.send('Page.navigate', { url });
  async function evaluate(expression) {
    const result = await cdp.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    assert.equal(result.exceptionDetails, undefined, JSON.stringify(result.exceptionDetails));
    return result.result.value;
  }
  for (let round = 0; round < 100; round++) {
    if (await evaluate('document.readyState === "complete"')) break;
    await pause(25);
  }
  await evaluate(`window.__authorized = true; window.__authorizations = 0; window.chrome = { runtime: { sendMessage: async (message) => { window.__authorizations++; return { ok: window.__authorized && message.command_id === 'command' }; } } };`);
  await evaluate(script);
  return { evaluate, run: async (action, payload = {}) => evaluate(`KulaApplicationRunner.run(${JSON.stringify(action)},${JSON.stringify(payload)},{id:'command',run_id:'run'})`) };
}

const form = `<form id="application" onsubmit="event.preventDefault(); document.body.innerHTML='<h1>Application submitted successfully</h1>'">
<label>Full name<input name="name" required></label><label>Email<input name="email" type="email" required></label>
<label>Work location<select name="location" required><option value="">Select</option><option value="remote">Remote</option></select></label>
<fieldset><legend>Contact preference</legend><label><input type="radio" name="contact" value="email" required>Email</label><label><input type="radio" name="contact" value="phone">Phone</label></fieldset>
<label>Skills<select name="skills" multiple><option value="js">JavaScript</option><option value="py">Python</option></select></label>
<label>Resume<input type="file" name="resume" required accept=".pdf"></label><button type="submit">Submit application</button></form>`;

test('reads, fills, uploads the matched PDF, and confirms a submitted standard application', async (t) => {
  const fixture = await page(t, form);
  const inspected = await fixture.run('inspect_form');
  assert.equal(inspected.next_action, 'submit');
  assert.equal(inspected.submit_ready, true);
  const answers = inspected.fields.filter((field) => field.type !== 'file').map((field) => ({ field_id: field.id,
    value: field.type === 'radio' ? 'email' : field.type === 'select' ? 'remote' : field.type === 'select-multiple' ? ['js', 'py'] : field.type === 'email' ? 'applicant@example.com' : 'Test Applicant' }));
  const filled = await fixture.run('fill_form', { form_token: inspected.form_token, answers,
    resume });
  assert.deepEqual(filled, { ok: true, validation_errors: [], resume_uploaded: true, resume_sha256: resume.sha256 });
  assert.deepEqual(await fixture.evaluate(`({name:document.querySelector('[name=name]').value,selected:[...document.querySelector('[name=skills]').selectedOptions].map(o=>o.value),resume:document.querySelector('[name=resume]').files[0].name})`),
    { name: 'Test Applicant', selected: ['js', 'py'], resume: 'matched-resume.pdf' });
  const submitted = await fixture.run('advance', { form_token: inspected.form_token, mode: 'submit' });
  assert.equal(submitted.submitted, true);
  assert.ok(await fixture.evaluate('window.__authorizations >= 7'));
});

test('a changed field layout invalidates the previous form token', async (t) => {
  const fixture = await page(t, form);
  const inspected = await fixture.run('inspect_form');
  await fixture.evaluate(`document.getElementById('application').insertAdjacentHTML('afterbegin','<label>New field<input name="extra" required></label>')`);
  const result = await fixture.run('fill_form', { form_token: inspected.form_token, answers: [] });
  assert.match(result.blocked, /changed/i);
  assert.equal(await fixture.evaluate('window.__authorizations'), 0);
});

test('Stop authorization prevents filling or submitting the application', async (t) => {
  const fixture = await page(t, form);
  const inspected = await fixture.run('inspect_form');
  await fixture.evaluate('window.__authorized = false');
  const answer = { field_id: inspected.fields[0].id, value: 'Must not be entered' };
  await assert.rejects(fixture.run('fill_form', { form_token: inspected.form_token, answers: [answer] }), /exception|undefined|stopped/i);
  assert.equal(await fixture.evaluate('document.querySelector("[name=name]").value'), '');
});

test('required consent and visible CAPTCHA are reported for manual attention', async (t) => {
  const consent = await page(t, form.replace('<button', '<label><input type="checkbox" required>I agree to the terms</label><button'));
  assert.match((await consent.run('inspect_form')).blocked, /consent|certification/i);
  const captcha = await page(t, form + '<div class="g-recaptcha">Verify you are human</div>');
  assert.match((await captcha.run('inspect_form')).blocked, /CAPTCHA|verification/i);
});

test('plain Jobright detail buttons launch the employer application once for both supported labels', async (t) => {
  for (const label of ['APPLY WITH AUTOFILL', 'EASY APPLY']) {
    const fixture = await page(t, `<button onclick="window.__clicks=(window.__clicks||0)+1;window.open('https://employer.example/apply')">${label}</button>`);
    await fixture.evaluate(`window.open = (url) => { window.__opened = url; return null; }`);
    assert.deepEqual(await fixture.run('open_entry'), { clicked: true });
    assert.equal(await fixture.evaluate('window.__opened'), 'https://employer.example/apply');
    assert.equal(await fixture.evaluate('window.__authorizations'), 1);
    assert.ok((await fixture.run('open_entry')).blocked, 'Do not click a launch control twice while waiting for its destination');
    assert.equal(await fixture.evaluate('window.__clicks'), 1);
  }
});

test('Jobright launch labels ignore external arrow decorations but reject additional action text', async (t) => {
  for (const label of [
    'APPLY WITH AUTOFILL ↗',
    'APPLY WITH AUTOFILL <span aria-hidden="true">↗</span>',
    'APPLY WITH AUTOFILL <svg><title>Open employer application</title></svg>',
  ]) {
    const fixture = await page(t, `<button onclick="window.__clicked=true">${label}</button>`);
    assert.equal((await fixture.run('open_entry')).clicked, true, `Decorated label: ${label}`);
    assert.equal(await fixture.evaluate('window.__clicked'), true);
  }
  const unrelated = await page(t, '<button onclick="window.__clicked=true">APPLY WITH AUTOFILL AND SUBMIT</button>');
  assert.ok((await unrelated.run('open_entry')).blocked);
  assert.equal(await unrelated.evaluate('Boolean(window.__clicked)'), false);
});

test('plain launcher exception is limited to Jobright detail pages and never clicks form submit controls', async (t) => {
  for (const [url, html] of [
    ['https://employer.example/apply', '<button onclick="window.__clicked=true">APPLY NOW</button>'],
    ['https://jobright.ai/jobs/recommend', '<button onclick="window.__clicked=true">EASY APPLY</button>'],
    ['https://jobright.ai/jobs/info/fixture', '<form><button onclick="event.preventDefault();window.__clicked=true">EASY APPLY</button></form>'],
    ['https://jobright.ai/jobs/info/fixture', '<button type="submit" onclick="window.__clicked=true">EASY APPLY</button>'],
  ]) {
    const fixture = await page(t, html, url);
    assert.match((await fixture.run('open_entry')).blocked, /submit immediately/i);
    assert.equal(await fixture.evaluate('Boolean(window.__clicked)'), false);
  }
});

test('disabled, ambiguous, or stopped Jobright launch controls cannot open an application', async (t) => {
  for (const html of [
    '<button disabled onclick="window.__clicked=true">EASY APPLY</button>',
    '<div role="button" aria-disabled="true" onclick="window.__clicked=true">APPLY WITH AUTOFILL</div>',
    '<button>EASY APPLY</button><button>APPLY WITH AUTOFILL</button>',
  ]) {
    const fixture = await page(t, html);
    assert.ok((await fixture.run('open_entry')).blocked);
    assert.equal(await fixture.evaluate('Boolean(window.__clicked)'), false);
  }
  const stopped = await page(t, '<button onclick="window.__clicked=true">EASY APPLY</button>');
  await stopped.evaluate('window.__authorized=false');
  await assert.rejects(stopped.run('open_entry'), /stopped|exception|undefined/i);
  assert.equal(await stopped.evaluate('Boolean(window.__clicked)'), false);
});

test('opening a marked apply dialog is allowed but private destination links are blocked', async (t) => {
  const dialog = await page(t, '<button aria-haspopup="dialog" onclick="window.__clicked=true">EASY APPLY</button>');
  assert.equal((await dialog.run('open_entry')).clicked, true);
  assert.equal(await dialog.evaluate('window.__clicked'), true);
  for (const host of ['127.0.0.1', 'localhost.', '100.64.0.1']) {
    const privateLink = await page(t, `<a href="https://${host}/private" onclick="event.preventDefault();window.__clicked=true">EASY APPLY</a>`);
    assert.match((await privateLink.run('open_entry')).blocked, /Unsupported/i);
    assert.equal(await privateLink.evaluate('Boolean(window.__clicked)'), false);
  }
});

test('missing required fields cannot reach a final submission click', async (t) => {
  const fixture = await page(t, form);
  const inspected = await fixture.run('inspect_form');
  const result = await fixture.run('advance', { form_token: inspected.form_token, mode: 'submit' });
  assert.match(result.blocked, /incomplete/i);
  assert.equal(await fixture.evaluate('window.__authorizations'), 0);
});

test('a form without a file input cannot acknowledge the matching resume', async (t) => {
  const fixture = await page(t, form.replace(/<label>Resume.*?<\/label>/, ''));
  const inspected = await fixture.run('inspect_form');
  const result = await fixture.run('fill_form', { form_token: inspected.form_token, answers: [], resume });
  assert.equal(result.resume_uploaded, false);
  assert.equal(result.resume_sha256, undefined);
});

test('a mismatched resume checksum is rejected before any file upload', async (t) => {
  const fixture = await page(t, form);
  const inspected = await fixture.run('inspect_form');
  const result = await fixture.run('fill_form', { form_token: inspected.form_token, answers: [], resume: { ...resume, sha256: '0'.repeat(64) } });
  assert.match(result.blocked, /checksum/i);
  assert.equal(await fixture.evaluate('document.querySelector("[name=resume]").files.length'), 0);
});

test('radio and checkbox answers use native clicks without toggling already selected answers again', async (t) => {
  const fixture = await page(t, form.replace('<button', '<label>Python experience<input name="python" type="checkbox"></label><button'));
  await fixture.evaluate(`window.__radioClicks=0;window.__checkboxClicks=0;
    document.querySelector('input[type=radio][value=email]').addEventListener('click',()=>window.__radioClicks++);
    document.querySelector('[name=python]').addEventListener('click',()=>window.__checkboxClicks++);`);
  const inspected = await fixture.run('inspect_form');
  const answers = [
    { field_id: inspected.fields.find((field) => field.type === 'radio').id, value: 'email' },
    { field_id: inspected.fields.find((field) => field.type === 'checkbox').id, value: true },
  ];
  for (let attempt = 0; attempt < 2; attempt++) {
    const result = await fixture.run('fill_form', { form_token: inspected.form_token, answers, resume });
    assert.equal(result.ok, true);
  }
  assert.deepEqual(await fixture.evaluate(`({radio:window.__radioClicks,checkbox:window.__checkboxClicks,
    selected:document.querySelector('input[type=radio][value=email]').checked,checked:document.querySelector('[name=python]').checked})`),
    { radio: 1, checkbox: 1, selected: true, checked: true });
});
