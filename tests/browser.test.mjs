import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { access, mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { after, before, test } from 'node:test';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const edgePath = process.env.EDGE_PATH || 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const origin = 'https://jobright.ai';
const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
let browserProcess;
let browserConnection;
let browserBaseUrl;
let browserProfile;
let contentScript;
let contentStyles;
let browserDiagnostics = '';
let closingBrowser = false;

class CdpConnection {
  constructor(socket) {
    this.socket = socket;
    this.nextId = 0;
    this.pending = new Map();
    this.listeners = new Map();
    socket.addEventListener('message', ({ data }) => {
      const message = JSON.parse(data);
      if (message.id) {
        const pending = this.pending.get(message.id);
        if (!pending) return;
        clearTimeout(pending.timer);
        this.pending.delete(message.id);
        if (message.error) pending.reject(new Error(JSON.stringify(message.error)));
        else pending.resolve(message.result);
      } else {
        for (const listener of this.listeners.get(message.method) || []) listener(message.params);
      }
    });
    socket.addEventListener('close', () => {
      for (const pending of this.pending.values()) {
        clearTimeout(pending.timer);
        pending.reject(new Error('Browser connection closed'));
      }
      this.pending.clear();
    });
  }

  static async connect(url) {
    const socket = new WebSocket(url);
    await new Promise((resolve, reject) => {
      socket.addEventListener('open', resolve, { once: true });
      socket.addEventListener('error', reject, { once: true });
    });
    return new CdpConnection(socket);
  }

  send(method, params = {}, timeout = 10_000) {
    const id = ++this.nextId;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`Timed out: ${method}`));
      }, timeout);
      this.pending.set(id, { resolve, reject, timer });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }

  on(method, listener) {
    const listeners = this.listeners.get(method) || [];
    listeners.push(listener);
    this.listeners.set(method, listeners);
  }

  close() {
    this.socket.close();
  }
}

before(async () => {
  await access(edgePath);
  [contentScript, contentStyles] = await Promise.all([
    readFile(path.join(root, 'extension', 'content.js'), 'utf8'),
    readFile(path.join(root, 'extension', 'content.css'), 'utf8'),
  ]);
  browserProfile = await mkdtemp(path.join(tmpdir(), 'jobright-filter-browser-'));
  browserProcess = spawn(edgePath, [
    '--headless=new',
    '--disable-gpu',
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-background-networking',
    '--disable-component-update',
    '--remote-debugging-port=0',
    `--user-data-dir=${browserProfile}`,
    'about:blank',
  ], { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  browserProcess.stdout.on('data', (chunk) => { browserDiagnostics += chunk; });
  browserProcess.stderr.on('data', (chunk) => { browserDiagnostics += chunk; });
  browserProcess.on('exit', (code, signal) => {
    if (!closingBrowser) console.error(`Test browser exited unexpectedly (code ${code}, signal ${signal}). ${browserDiagnostics}`);
  });
  let launchError;
  browserProcess.on('error', (error) => { launchError = error; });
  const deadline = Date.now() + 20_000;
  while (Date.now() < deadline) {
    if (launchError) throw launchError;
    try {
      const activePort = await readFile(path.join(browserProfile, 'DevToolsActivePort'), 'utf8');
      const [port, endpoint] = activePort.trim().split(/\r?\n/);
      browserBaseUrl = `http://127.0.0.1:${port}`;
      browserConnection = await CdpConnection.connect(`ws://127.0.0.1:${port}${endpoint}`);
      return;
    } catch {
      await delay(100);
    }
  }
  throw new Error(`Edge did not start its debugging endpoint within 20 seconds (exit ${browserProcess.exitCode}). ${browserDiagnostics}`);
});

after(async () => {
  closingBrowser = true;
  if (browserConnection) {
    await browserConnection.send('Browser.close').catch(() => {});
    browserConnection.close();
  }
  if (browserProcess && browserProcess.exitCode === null) {
    await Promise.race([
      new Promise((resolve) => browserProcess.once('exit', resolve)),
      delay(5_000),
    ]);
    if (browserProcess.exitCode === null) browserProcess.kill();
  }
  if (browserProfile) {
    const resolvedProfile = path.resolve(browserProfile);
    const allowedPrefix = path.join(path.resolve(tmpdir()), 'jobright-filter-browser-');
    assert.ok(resolvedProfile.startsWith(allowedPrefix), 'Only remove the isolated browser test profile');
    await rm(resolvedProfile, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
});

function card(id, label, tag = 'article') {
  return `<${tag} id="${id}" data-job-id="${id}" class="job-card">
    <div class="job-card-content">
    <h2><a href="/jobs/info/${id}">Software Engineer at ${id}</a></h2>
    <p>Remote · Full-time</p><button class="apply">${label}</button>
    </div>
    <aside class="match-score">90% STRONG MATCH</aside>
  </${tag}>`;
}

const fixture = `<!doctype html><html><head><title>Jobright filter browser test</title></head>
<body><header id="header">Jobs</header><main id="main">
<section id="job-list" aria-label="Recommended jobs">
${card('autofill', 'APPLY WITH AUTOFILL')}
${card('easy', 'EASY APPLY')}
${card('linkedin', 'APPLY NOW')}
${card('nested', '<span>  apply\n</span><span> now </span>')}
<article id="prose"><h2>Application tips</h2><p>Apply now to get a head start.</p><button>Read more</button></article>
</section><aside id="job-detail"><h1>Selected job</h1><p>Full job description</p><button>APPLY NOW</button></aside>
</main><footer id="footer">End of page</footer></body></html>`;

const storageMock = `(() => {
  const state = { hideApplyNow: true, hideDuplicates: true };
  const listeners = [];
  const messageListeners = [];
  window.__requestJobLinks = () => new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error('No job-link response')), 130_000);
    for (const listener of messageListeners) {
      listener({ type: 'GET_JOB_LINKS' }, {}, (response) => {
        clearTimeout(timeout);
        resolve(response);
      });
    }
  });
  window.__setSettings = (values) => {
    const changes = {};
    for (const [key, newValue] of Object.entries(values)) {
      changes[key] = { oldValue: state[key], newValue };
      state[key] = newValue;
    }
    for (const listener of listeners) listener(changes, 'local');
  };
  window.__setFilterEnabled = (enabled) => window.__setSettings({ hideApplyNow: enabled });
  window.chrome = {
    storage: {
      local: {
        get(keys, callback) {
          const result = typeof keys === 'object' && !Array.isArray(keys) && keys ? { ...keys, ...state } : { ...state };
          if (callback) queueMicrotask(() => callback(result));
          return Promise.resolve(result);
        },
        set(values, callback) {
          window.__setSettings(values);
          if (callback) queueMicrotask(callback);
          return Promise.resolve();
        }
      },
      onChanged: { addListener(listener) { listeners.push(listener); } }
    },
    runtime: {
      lastError: undefined,
      onMessage: { addListener(listener) { messageListeners.push(listener); } },
      sendMessage() { return Promise.resolve(); }
    }
  };
})();`;

async function createPage(t, html = fixture, url = `${origin}/jobs/recommend`, initiallyEnabled = true) {
  const response = await fetch(`${browserBaseUrl}/json/new?about:blank`, { method: 'PUT' });
  assert.equal(response.ok, true);
  const target = await response.json();
  const connection = await CdpConnection.connect(target.webSocketDebuggerUrl);
  t.after(async () => {
    connection.close();
    await browserConnection.send('Target.closeTarget', { targetId: target.id }).catch(() => {});
  });
  await connection.send('Page.enable');
  await connection.send('Runtime.enable');
  await connection.send('Fetch.enable', { patterns: [{ urlPattern: `${origin}/*` }] });
  connection.on('Fetch.requestPaused', ({ requestId, resourceType }) => {
    connection.send('Fetch.fulfillRequest', {
      requestId,
      responseCode: resourceType === 'Document' ? 200 : 204,
      responseHeaders: [{ name: 'Content-Type', value: 'text/html; charset=utf-8' }],
      body: Buffer.from(resourceType === 'Document' ? html : '').toString('base64'),
    }).catch(() => {});
  });
  await connection.send('Page.navigate', { url });
  const page = {
    async resize(width, height = 800) {
      await connection.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false });
    },
    async evaluate(expression) {
      const result = await connection.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }, 135_000);
      assert.equal(result.exceptionDetails, undefined, JSON.stringify(result.exceptionDetails));
      return result.result.value;
    },
    async eventually(expression, message) {
      const deadline = Date.now() + 5_000;
      do {
        if (await this.evaluate(expression)) return;
        await delay(25);
      } while (Date.now() < deadline);
      assert.fail(message || `Condition did not become true: ${expression}`);
    },
  };
  await page.eventually('document.readyState === "complete" && Boolean(document.querySelector("#job-list"))', 'Test fixture loaded');
  await page.evaluate(storageMock);
  if (typeof initiallyEnabled === 'object') {
    await page.evaluate(`window.__setSettings(${JSON.stringify(initiallyEnabled)})`);
  } else if (!initiallyEnabled) {
    await page.evaluate('window.__setFilterEnabled(false)');
  }
  await page.evaluate(`(() => { const style = document.createElement('style'); style.textContent = ${JSON.stringify(contentStyles)}; document.head.append(style); })()`);
  await page.evaluate(contentScript);
  return page;
}

const hidden = (id) => `document.getElementById(${JSON.stringify(id)})?.getAttribute('data-jobright-apply-filter-hidden') === 'true' && getComputedStyle(document.getElementById(${JSON.stringify(id)})).display === 'none'`;
const visible = (id) => `document.getElementById(${JSON.stringify(id)})?.getAttribute('data-jobright-apply-filter-hidden') !== 'true' && getComputedStyle(document.getElementById(${JSON.stringify(id)})).display !== 'none'`;

function duplicateCard(id, {
  jobId = id,
  href = `/jobs/info/${jobId}`,
  title = 'Software Engineer',
  company = 'Acme',
  location = 'Remote',
  label = 'EASY APPLY',
} = {}) {
  return `<article id="${id}" data-job-id="${jobId}" class="job-card">
    <h2 data-testid="job-title">${href ? `<a href="${href}">${title}</a>` : title}</h2>
    ${company === null ? '' : `<p data-testid="company-name">${company}</p>`}
    ${location === null ? '' : `<p data-testid="job-location">${location}</p>`}
    <button class="apply">${label}</button>
    <aside class="match-score">90% STRONG MATCH</aside>
  </article>`;
}

const duplicateFixture = (...cards) => `<!doctype html><html><body>
  <main><section id="job-list">${cards.join('')}</section></main>
</body></html>`;

test('hides complete APPLY NOW cards, preserving other application types, prose, detail, and layout', async (t) => {
  const page = await createPage(t);
  await page.eventually(hidden('linkedin'), 'APPLY NOW job card should be hidden');
  await page.eventually(hidden('nested'), 'Nested, case-insensitive button text should be recognized');
  for (const id of ['autofill', 'easy', 'prose', 'job-detail', 'job-list', 'main', 'header', 'footer']) {
    assert.equal(await page.evaluate(visible(id)), true, `${id} stays visible`);
  }
  assert.equal(await page.evaluate('document.querySelector("#linkedin .match-score").getClientRects().length'), 0, 'Whole card, including match panel, is hidden');
  assert.equal(await page.evaluate('document.querySelectorAll("#job-list > article").length'), 5, 'Filtering preserves the DOM for reversible restoration');
});

test('handles jobs loaded asynchronously and button labels updated on reused cards', async (t) => {
  const page = await createPage(t);
  await page.eventually(hidden('linkedin'));
  await page.evaluate(`document.getElementById('job-list').insertAdjacentHTML('beforeend', ${JSON.stringify(card('async-job', 'APPLY NOW'))})`);
  await page.eventually(hidden('async-job'), 'Newly loaded APPLY NOW card should be hidden');
  await page.evaluate(`document.querySelector('#async-job .apply').textContent = 'EASY APPLY'`);
  await page.eventually(visible('async-job'), 'A reused card relabeled EASY APPLY should be restored');
  await page.evaluate(`document.querySelector('#easy .apply').firstChild.data = 'APPLY NOW'`);
  await page.eventually(hidden('easy'), 'Character-data changes should reclassify visible cards');
  await page.evaluate(`document.querySelector('#linkedin .apply').remove()`);
  await page.eventually(visible('linkedin'), 'Removing a reused card’s apply button should restore the card');
});

test('changing the stored toggle restores and re-hides jobs without reloading', async (t) => {
  const page = await createPage(t);
  await page.eventually(hidden('linkedin'));
  await page.evaluate('window.__setFilterEnabled(false)');
  await page.eventually(visible('linkedin'), 'Disabling the filter should restore hidden jobs');
  await page.eventually(visible('nested'));
  await page.evaluate(`document.getElementById('job-list').insertAdjacentHTML('beforeend', ${JSON.stringify(card('while-disabled', 'APPLY NOW'))})`);
  await delay(200);
  assert.equal(await page.evaluate(visible('while-disabled')), true, 'Jobs added while disabled remain visible');
  await page.evaluate('window.__setFilterEnabled(true)');
  await page.eventually(hidden('linkedin'));
  await page.eventually(hidden('while-disabled'), 'Re-enabling should include jobs added while disabled');
});

test('supports semantic list cards without custom classes and leaves sibling cards visible', async (t) => {
  const html = `<!doctype html><html><body><ul id="job-list">
    <li id="semantic-now"><h3><a href="/jobs/info/semantic-now">Engineer</a></h3><button>APPLY NOW</button></li>
    <li id="semantic-easy"><h3><a href="/jobs/info/semantic-easy">Engineer</a></h3><button>EASY APPLY</button></li>
  </ul><section id="job-detail"><h1>Engineer</h1><button>APPLY NOW</button></section></body></html>`;
  const page = await createPage(t, html);
  await page.eventually(hidden('semantic-now'), 'Semantic list item should be recognized as the complete job card');
  for (const id of ['semantic-easy', 'job-list', 'job-detail']) {
    assert.equal(await page.evaluate(visible(id)), true, `${id} stays visible`);
  }
});

test('hides outer cards with score panels and nested footer buttons while preserving promotion articles', async (t) => {
  const html = `<!doctype html><html><body><main><div id="job-list">
    <div id="outer-card" class="job-card"><article class="job-card-content">
      <h2><a href="/jobs/info/outer-card">Engineer</a></h2>
      <footer><button>APPLY NOW</button></footer>
    </article><div id="outer-score">95% STRONG MATCH</div></div>
    <div id="generic-now"><div><h2><a href="/jobs/info/generic-now">Engineer</a></h2><button>APPLY NOW</button></div><div id="generic-score">92% STRONG MATCH</div></div>
    <div id="generic-easy"><div><h2><a href="/jobs/info/generic-easy">Engineer</a></h2><button>EASY APPLY</button></div><div>90% STRONG MATCH</div></div>
    <article id="promotion"><h2>Apply for our newsletter</h2><button>APPLY NOW</button></article>
  </div></main></body></html>`;
  const page = await createPage(t, html);
  await page.eventually(hidden('outer-card'), 'The named outer card should hide with its score column');
  await page.eventually(hidden('generic-now'), 'An unknown card layout with sibling jobs should hide as a complete card');
  for (const id of ['outer-score', 'generic-score']) {
    assert.equal(await page.evaluate(`document.getElementById(${JSON.stringify(id)}).getClientRects().length`), 0, `${id} disappears with its card`);
  }
  for (const id of ['generic-easy', 'promotion', 'job-list']) {
    assert.equal(await page.evaluate(visible(id)), true, `${id} stays visible`);
  }
});

test('respects a saved disabled preference on startup', async (t) => {
  const page = await createPage(t, fixture, `${origin}/jobs/recommend`, false);
  await delay(200);
  assert.equal(await page.evaluate(visible('linkedin')), true, 'Saved preference is loaded before initial filtering');
  await page.evaluate('window.__setFilterEnabled(true)');
  await page.eventually(hidden('linkedin'), 'A disabled startup can subsequently be enabled');
});

test('popup loads both preferences, saves independent changes, and tracks changes from other tabs', async (t) => {
  const [popupHtml, popupScript] = await Promise.all([
    readFile(path.join(root, 'extension', 'popup.html'), 'utf8'),
    readFile(path.join(root, 'extension', 'popup.js'), 'utf8'),
  ]);
  const page = await createPage(t, popupHtml.replace('<main>', '<main id="job-list">'), `${origin}/popup`, false);
  await page.evaluate(popupScript);
  await page.eventually('document.getElementById("hide-apply-now").disabled === false');
  await page.eventually('document.getElementById("hide-duplicates").disabled === false');
  assert.equal(await page.evaluate('document.getElementById("hide-apply-now").checked'), false, 'Popup reads disabled preference');
  assert.equal(await page.evaluate('document.getElementById("hide-duplicates").checked'), true, 'Duplicate filtering defaults to enabled');
  await page.evaluate('document.getElementById("hide-apply-now").click()');
  await page.eventually('document.getElementById("hide-apply-now").disabled === false && document.getElementById("hide-apply-now").checked');
  assert.equal(await page.evaluate('(async () => (await chrome.storage.local.get("hideApplyNow")).hideApplyNow)()'), true, 'Popup saves the enabled preference');
  await page.evaluate('document.getElementById("hide-duplicates").click()');
  await page.eventually('document.getElementById("hide-duplicates").disabled === false && document.getElementById("hide-duplicates").checked === false');
  assert.deepEqual(await page.evaluate('chrome.storage.local.get(["hideApplyNow", "hideDuplicates"])'), {
    hideApplyNow: true,
    hideDuplicates: false,
  }, 'Changing the duplicate preference leaves the APPLY NOW preference enabled');
  await page.evaluate('window.__setFilterEnabled(false)');
  await page.eventually('document.getElementById("hide-apply-now").checked === false');
  await page.evaluate('window.__setSettings({ hideDuplicates: true })');
  await page.eventually('document.getElementById("hide-duplicates").checked === true');
  assert.equal(await page.evaluate('document.getElementById("hide-apply-now").checked'), false, 'Other-tab duplicate changes leave the APPLY NOW preference disabled');
});

test('restores filtered cards when navigation leaves the jobs section', async (t) => {
  const page = await createPage(t);
  await page.eventually(hidden('linkedin'));
  await page.evaluate(`history.pushState({}, '', '/profile')`);
  await page.eventually(visible('linkedin'), 'Leaving /jobs should restore filtered content');
  await page.evaluate(`history.pushState({}, '', '/jobs/recommend'); window.dispatchEvent(new PopStateEvent('popstate'))`);
  await page.eventually(hidden('linkedin'), 'Returning to job recommendations should reapply the filter');
});

test('deduplicates canonical job URLs and job IDs without requiring company or location metadata', async (t) => {
  const html = duplicateFixture(
    duplicateCard('url-first', { jobId: 'card-a', href: '/jobs/info/shared-job', company: null, location: null }),
    duplicateCard('url-query', { jobId: 'card-b', href: 'https://jobright.ai/jobs/info/shared-job?source=recommend#apply', company: null, location: null }),
    duplicateCard('url-slash', { jobId: 'card-c', href: '/jobs/info/shared-job/?source=search', company: null, location: null }),
    duplicateCard('id-first', { jobId: 'id-only-job', href: null, company: null, location: null }),
    duplicateCard('id-copy', { jobId: 'id-only-job', href: null, company: null, location: null }),
    duplicateCard('unknown-first', { company: null, location: null }),
    duplicateCard('unknown-second', { company: null, location: null }),
  );
  const page = await createPage(t, html);
  for (const id of ['url-query', 'url-slash', 'id-copy']) {
    await page.eventually(hidden(id), `${id} identifies a previously listed job`);
  }
  for (const id of ['url-first', 'id-first', 'unknown-first', 'unknown-second', 'job-list']) {
    assert.equal(await page.evaluate(visible(id)), true, `${id} stays visible`);
  }
  assert.equal(await page.evaluate('document.querySelector("#url-query .match-score").getClientRects().length'), 0, 'Duplicate filtering hides the whole card and its score panel');
});

test('matches normalized title, company, and location while preserving different or incomplete identities', async (t) => {
  const html = duplicateFixture(
    duplicateCard('original'),
    duplicateCard('repost', { title: '  SOFTWARE   engineer ', company: ' acme ', location: ' REMOTE ', label: 'APPLY WITH AUTOFILL' }),
    duplicateCard('different-company', { company: 'Other Company' }),
    duplicateCard('different-location', { location: 'New York, NY' }),
    duplicateCard('different-role', { title: 'Senior Software Engineer' }),
    duplicateCard('missing-location-one', { location: null }),
    duplicateCard('missing-location-two', { location: null }),
    duplicateCard('missing-company-one', { company: null }),
    duplicateCard('missing-company-two', { company: null }),
  );
  const page = await createPage(t, html);
  await page.eventually(hidden('repost'), 'Matching metadata should identify a repost even with a different job ID');
  for (const id of ['original', 'different-company', 'different-location', 'different-role', 'missing-location-one', 'missing-location-two', 'missing-company-one', 'missing-company-two']) {
    assert.equal(await page.evaluate(visible(id)), true, `${id} has a distinct or incomplete identity`);
  }
});

test('deduplicates asynchronously loaded jobs and restores a duplicate when the first card is removed', async (t) => {
  const page = await createPage(t, duplicateFixture(duplicateCard('retained'), duplicateCard('other', { title: 'Designer' })));
  await page.evaluate(`document.getElementById('job-list').insertAdjacentHTML('beforeend', ${JSON.stringify(duplicateCard('appended'))})`);
  await page.eventually(hidden('appended'), 'An asynchronously loaded repost should be hidden');
  assert.equal(await page.evaluate(visible('retained')), true, 'The first eligible card remains visible');
  await page.evaluate('document.getElementById("retained").remove()');
  await page.eventually(visible('appended'), 'The next matching card should be restored after the retained card is removed');
  assert.equal(await page.evaluate(visible('other')), true, 'An unrelated job remains visible');
});

test('reclassifies reused cards when their URL, job ID, or identity metadata changes', async (t) => {
  const html = duplicateFixture(
    duplicateCard('url-source', { href: '/jobs/info/url-shared', company: null, location: null }),
    duplicateCard('url-reused', { href: '/jobs/info/url-shared', company: null, location: null }),
    duplicateCard('id-source', { jobId: 'shared-id', href: null, company: null, location: null }),
    duplicateCard('id-reused', { jobId: 'shared-id', href: null, company: null, location: null }),
    duplicateCard('metadata-source'),
    duplicateCard('metadata-reused'),
  );
  const page = await createPage(t, html);
  for (const id of ['url-reused', 'id-reused', 'metadata-reused']) await page.eventually(hidden(id));
  await page.evaluate(`document.querySelector('#url-reused a').setAttribute('href', '/jobs/info/new-url')`);
  await page.eventually(visible('url-reused'), 'Changing only the job URL should restore a reused card');
  await page.evaluate(`document.getElementById('id-reused').setAttribute('data-job-id', 'new-id')`);
  await page.eventually(visible('id-reused'), 'Changing only the job ID should restore a reused card');
  await page.evaluate(`document.querySelector('#metadata-reused [data-testid="company-name"]').firstChild.data = 'Another Company'`);
  await page.eventually(visible('metadata-reused'), 'Changing company text should restore a reused card');
  await page.evaluate(`document.querySelector('#metadata-reused [data-testid="company-name"]').textContent = 'Acme'`);
  await page.eventually(hidden('metadata-reused'), 'Changing identity metadata back should hide a duplicate again');
  await page.evaluate(`document.querySelector('#metadata-reused [data-testid="job-location"]').remove()`);
  await page.eventually(visible('metadata-reused'), 'Removing required metadata should stop matching different job IDs');
});

test('keeps the first eligible duplicate when an earlier APPLY NOW card is filtered', async (t) => {
  const html = duplicateFixture(
    duplicateCard('excluded-first', { label: 'APPLY NOW' }),
    duplicateCard('eligible-first'),
    duplicateCard('eligible-copy', { label: 'APPLY WITH AUTOFILL' }),
  );
  const page = await createPage(t, html);
  await page.eventually(hidden('excluded-first'));
  await page.eventually(hidden('eligible-copy'));
  assert.equal(await page.evaluate(visible('eligible-first')), true, 'A hidden APPLY NOW card must not suppress the usable application');
  await page.evaluate('window.__setSettings({ hideApplyNow: false })');
  await page.eventually(visible('excluded-first'), 'Allowing APPLY NOW makes the first card eligible');
  await page.eventually(hidden('eligible-first'), 'The duplicate filter still runs independently');
  await page.evaluate('window.__setSettings({ hideDuplicates: false })');
  for (const id of ['excluded-first', 'eligible-first', 'eligible-copy']) await page.eventually(visible(id));
  await page.evaluate('window.__setSettings({ hideApplyNow: true })');
  await page.eventually(hidden('excluded-first'));
  for (const id of ['eligible-first', 'eligible-copy']) {
    assert.equal(await page.evaluate(visible(id)), true, 'Turning on APPLY NOW filtering leaves duplicates visible when their setting is off');
  }
  await page.evaluate('window.__setSettings({ hideDuplicates: true })');
  await page.eventually(hidden('eligible-copy'));
  assert.equal(await page.evaluate(visible('eligible-first')), true, 'Re-enabling duplicate filtering keeps the first eligible card');
});

test('respects saved duplicate settings independently on startup', async (t) => {
  const html = duplicateFixture(
    duplicateCard('startup-first', { label: 'APPLY NOW' }),
    duplicateCard('startup-copy'),
  );
  const page = await createPage(t, html, `${origin}/jobs/recommend`, { hideApplyNow: false, hideDuplicates: false });
  await delay(200);
  for (const id of ['startup-first', 'startup-copy']) {
    assert.equal(await page.evaluate(visible(id)), true, 'Saved disabled preferences restore all jobs on startup');
  }
  await page.evaluate('window.__setSettings({ hideDuplicates: true })');
  await page.eventually(hidden('startup-copy'), 'Duplicate filtering can be enabled while APPLY NOW filtering remains off');
  assert.equal(await page.evaluate(visible('startup-first')), true);
});

test('restores duplicate cards outside job listings and recalculates them on return', async (t) => {
  const page = await createPage(t, duplicateFixture(duplicateCard('route-first'), duplicateCard('route-copy')));
  await page.eventually(hidden('route-copy'));
  await page.evaluate(`history.pushState({}, '', '/profile')`);
  await page.eventually(visible('route-copy'), 'Leaving job listings restores duplicates');
  await page.evaluate(`history.pushState({}, '', '/jobs/recommend'); window.dispatchEvent(new PopStateEvent('popstate'))`);
  await page.eventually(hidden('route-copy'), 'Returning to job listings recalculates duplicates');
  assert.equal(await page.evaluate(visible('route-first')), true);
});

test('keeps a visible duplicate when site visibility changes, including responsive resize', async (t) => {
  const html = duplicateFixture(
    duplicateCard('original', { jobId: 'responsive' }),
    duplicateCard('copy', { jobId: 'responsive' }),
  ).replace('</body>', '<style>@media (max-width: 400px) { #original { display: none; } }</style></body>');
  const page = await createPage(t, html);
  await page.resize(1000);
  await page.eventually(hidden('copy'));
  await page.evaluate('document.getElementById("original").hidden = true');
  await page.eventually(visible('copy'), 'A native-hidden original must not suppress its visible copy');
  await page.evaluate('document.getElementById("original").hidden = false');
  await page.eventually(hidden('copy'));
  await page.resize(350);
  await page.eventually(visible('copy'), 'A media-query-hidden original must not suppress its visible copy');
  await page.resize(1000);
  await page.eventually(hidden('copy'), 'Resizing back should keep only the first visible card again');
});

test('includes separate geographic labels when matching remote reposts', async (t) => {
  const withCountry = (id, country) => duplicateCard(id, { location: 'Remote' })
    .replace('<button', `<span class="location-country">${country}</span><button`);
  const page = await createPage(t, duplicateFixture(
    withCountry('us-first', 'United States'),
    withCountry('canada', 'Canada'),
    withCountry('us-copy', 'United States'),
  ));
  await page.eventually(hidden('us-copy'));
  assert.equal(await page.evaluate(visible('us-first')), true);
  assert.equal(await page.evaluate(visible('canada')), true, 'Remote jobs with different geography remain visible');
});

test('exports canonical links only from cards retained by the current filters, including cards below the viewport', async (t) => {
  const html = duplicateFixture(
    duplicateCard('apply-now', { label: 'APPLY NOW' }),
    duplicateCard('retained', { href: '/jobs/info/retained?source=recommend#apply' }),
    duplicateCard('repost', { href: '/jobs/info/repost', label: 'APPLY WITH AUTOFILL' }),
    '<div style="height: 1800px"></div>',
    duplicateCard('below-fold', { title: 'Designer', href: 'https://www.jobright.ai/jobs/info/below-fold/?source=search' }),
  );
  const page = await createPage(t, html);
  await page.eventually(hidden('apply-now'));
  await page.eventually(hidden('repost'));
  assert.equal(await page.evaluate('document.getElementById("below-fold").getBoundingClientRect().top > innerHeight'), true);
  assert.deepEqual((await page.evaluate('window.__requestJobLinks()')).links, [
    `${origin}/jobs/info/retained`,
    `${origin}/jobs/info/below-fold`,
  ], 'Download follows both filters and strips tracking parameters, fragment, www, and trailing slash');
});

test('export refreshes new cards immediately and follows disabled filters while deduplicating identical links', async (t) => {
  const page = await createPage(t);
  await page.eventually(hidden('linkedin'));
  const inserted = duplicateCard('instant-now', { title: 'Another job', label: 'APPLY NOW' });
  const response = await page.evaluate(`(() => {
    document.getElementById('job-list').insertAdjacentHTML('beforeend', ${JSON.stringify(inserted)});
    return window.__requestJobLinks();
  })()`);
  assert.deepEqual(response.links, [`${origin}/jobs/info/autofill`, `${origin}/jobs/info/easy`], 'A newly inserted APPLY NOW card is excluded before the debounced scan runs');
  await page.evaluate('window.__setSettings({ hideApplyNow: false, hideDuplicates: false })');
  const duplicate = card('same-easy', 'EASY APPLY').replace('/jobs/info/same-easy', '/jobs/info/easy?source=copy');
  const unfiltered = await page.evaluate(`(() => {
    document.getElementById('job-list').insertAdjacentHTML('beforeend', ${JSON.stringify(duplicate)});
    return window.__requestJobLinks();
  })()`);
  assert.deepEqual(unfiltered.links, [
    `${origin}/jobs/info/autofill`, `${origin}/jobs/info/easy`,
    `${origin}/jobs/info/linkedin`, `${origin}/jobs/info/nested`, `${origin}/jobs/info/instant-now`,
  ], 'Disabled filters allow APPLY NOW cards but the downloaded file never repeats a URL');
});

test('export excludes site-hidden cards, detail panels, navigation links, and offsite application URLs', async (t) => {
  const html = duplicateFixture(
    card('eligible', 'EASY APPLY'),
    `<div hidden>${card('site-hidden', 'EASY APPLY')}</div>`,
    card('css-hidden', 'EASY APPLY').replace('<article', '<article style="display:none"'),
    duplicateCard('external', { href: 'https://example.com/jobs/info/external', company: null, location: null }),
    '<nav><a href="/jobs/info/navigation">Navigation link</a></nav>',
    `<aside data-testid="job-detail">${card('detail-card', 'EASY APPLY')}</aside>`,
  );
  const page = await createPage(t, html);
  assert.deepEqual((await page.evaluate('window.__requestJobLinks()')).links, [`${origin}/jobs/info/eligible`]);
});

test('export supports a reliable data-job-id when a card has no link and skips ambiguous or unknown IDs', async (t) => {
  const jobId = 'ABCDEF0123456789ABCDEF01';
  const html = duplicateFixture(
    duplicateCard('data-only', { href: null, jobId, company: null, location: null }),
    duplicateCard('unknown-id', { href: null, jobId: 'dom-row-17', company: null, location: null }),
    duplicateCard('ambiguous', { href: null, jobId: '111111111111111111111111', company: null, location: null })
      .replace('</h2>', '</h2><span data-job-id="222222222222222222222222"></span>'),
  );
  const page = await createPage(t, html);
  assert.deepEqual((await page.evaluate('window.__requestJobLinks()')).links, [`${origin}/jobs/info/${jobId.toLowerCase()}`]);
});

test('export refuses non-list routes even when old job cards remain in the page', async (t) => {
  const page = await createPage(t);
  await page.eventually(hidden('linkedin'));
  for (const route of ['/profile', '/jobs/info/selected']) {
    await page.evaluate(`history.pushState({}, '', ${JSON.stringify(route)})`);
    const result = await page.evaluate('window.__requestJobLinks()');
    assert.ok(result.error, `Route ${route} should explain why links cannot be exported`);
    assert.ok(!result.links?.length, 'Do not export stale listing cards from another route');
  }
});

test('automatic collection follows a nested scrolling list and retains links when virtualized cards are replaced', async (t) => {
  const initial = [
    duplicateCard('virtual-one', { title: 'Engineer' }),
    duplicateCard('virtual-two', { title: 'Designer' }),
  ];
  const nextBatch = [
    duplicateCard('virtual-two-copy', { jobId: 'virtual-two', title: 'Designer' }),
    duplicateCard('virtual-one-repost', { title: 'Engineer' }),
    duplicateCard('virtual-three', { title: 'Analyst', label: 'APPLY WITH AUTOFILL' }),
    duplicateCard('virtual-external-apply', { title: 'Scientist', label: 'APPLY NOW' }),
  ];
  const html = duplicateFixture(...initial).replace('</body>', '<style>#job-list { height: 240px; overflow-y: auto; } .job-card { min-height: 180px; }</style></body>');
  const page = await createPage(t, html);
  await page.evaluate(`(() => {
    const list = document.getElementById('job-list');
    window.__loadedNextBatch = false;
    list.addEventListener('scroll', () => {
      if (!window.__loadedNextBatch && list.scrollTop + list.clientHeight >= list.scrollHeight - 2) {
        window.__loadedNextBatch = true;
        list.innerHTML = ${JSON.stringify(nextBatch.join(''))};
      }
    });
  })()`);
  const result = await page.evaluate('window.__requestJobLinks()');
  assert.equal(await page.evaluate('window.__loadedNextBatch'), true, 'Download scrolls the actual nested list to request more cards');
  assert.deepEqual(result.links, [
    `${origin}/jobs/info/virtual-one`, `${origin}/jobs/info/virtual-two`, `${origin}/jobs/info/virtual-three`,
  ], 'Earlier links survive virtualization and repeated jobs stay excluded across batches');
});

test('automatic collection visits every short virtualized row from the top and restores the original scroll position', async (t) => {
  const rows = Array.from({ length: 18 }, (_, index) => card(`short-row-${index}`, 'EASY APPLY'));
  const html = duplicateFixture('<div id="virtual-content"></div>').replace('</body>', `<style>
    #job-list { height: 180px; overflow-y: auto; }
    #virtual-content { position: relative; height: ${rows.length * 60}px; }
    .job-card { position: absolute; height: 60px; width: 100%; margin: 0; overflow: hidden; box-sizing: border-box; }
    .job-card h2, .job-card p { margin: 0; font-size: 12px; }
    .match-score { display: none; }
  </style></body>`);
  const page = await createPage(t, html);
  await page.evaluate(`(() => {
    const list = document.getElementById('job-list');
    const content = document.getElementById('virtual-content');
    const rows = ${JSON.stringify(rows)};
    const render = () => {
      const first = Math.floor(list.scrollTop / 60);
      content.innerHTML = rows.slice(first, first + 3).join('');
      [...content.children].forEach((row, index) => { row.style.top = ((first + index) * 60) + 'px'; });
    };
    list.addEventListener('scroll', render);
    list.scrollTop = 360;
    render();
  })()`);
  assert.equal(await page.evaluate('document.getElementById("short-row-0") === null'), true, 'Earlier jobs are not mounted when collection begins');
  const result = await page.evaluate('window.__requestJobLinks()');
  assert.deepEqual(result.links, rows.map((_, index) => `${origin}/jobs/info/short-row-${index}`), 'Scroll increments must not jump over short rows removed by virtualization');
  assert.equal(await page.evaluate('document.getElementById("job-list").scrollTop'), 360, 'The original list position is restored after collection');
});

test('automatic collection keeps waiting through successive filtered batches before an eligible job arrives', async (t) => {
  const batches = Array.from({ length: 8 }, (_, index) => card(`excluded-batch-${index}`, 'APPLY NOW'));
  batches.push(card('late-eligible', 'EASY APPLY'));
  const page = await createPage(t, duplicateFixture(batches[0]));
  await page.eventually(hidden('excluded-batch-0'));
  const result = await page.evaluate(`(() => {
    const batches = ${JSON.stringify(batches)};
    window.__loadedBatch = 0;
    const timer = setInterval(() => {
      window.__loadedBatch++;
      document.getElementById('job-list').innerHTML = batches[window.__loadedBatch];
      if (window.__loadedBatch === batches.length - 1) clearInterval(timer);
    }, 650);
    return window.__requestJobLinks().finally(() => clearInterval(timer));
  })()`);
  assert.equal(await page.evaluate('window.__loadedBatch'), batches.length - 1, 'Filtered jobs arriving for more than six collection polls still count as loading progress');
  assert.deepEqual(result.links, [`${origin}/jobs/info/late-eligible`], 'The export includes the later usable job and excludes every APPLY NOW batch');
});

test('automatic collection cancels when the user changes filters or navigates away', async (t) => {
  const page = await createPage(t);
  await page.eventually(hidden('linkedin'));
  const result = await page.evaluate(`(() => {
    const pending = window.__requestJobLinks();
    setTimeout(() => window.__setSettings({ hideApplyNow: false }), 100);
    return pending;
  })()`);
  assert.ok(result.error, 'Do not label a mixture of different filter states as one complete export');
  const navigated = await page.evaluate(`(() => {
    const pending = window.__requestJobLinks();
    setTimeout(() => history.pushState({}, '', '/profile'), 100);
    return pending;
  })()`);
  assert.ok(navigated.error, 'Navigating away cancels the active export');
});

async function createDownloadPopup(t, pairingKey = '') {
  const [html, script] = await Promise.all([
    readFile(path.join(root, 'extension', 'popup.html'), 'utf8'),
    readFile(path.join(root, 'extension', 'popup.js'), 'utf8'),
  ]);
  const page = await createPage(t, html.replace('<main>', '<main id="job-list">'), `${origin}/popup`);
  await page.evaluate(`(() => {
    window.__setSettings({ kulaPairingKey: ${JSON.stringify(pairingKey)} });
    window.__downloadCalls = [];
    window.__tabQueries = [];
    window.__messages = [];
    window.__tabResult = [{ id: 42, url: 'https://jobright.ai/jobs/recommend' }];
    window.__exportResult = { links: [] };
    window.__exportError = null;
    window.__downloadBlob = null;
    window.__runtimeMessages = [];
    window.__kulaResult = { ok: true, count: 0 };
    window.__kulaError = null;
    chrome.runtime.sendMessage = async (message) => {
      window.__runtimeMessages.push(message);
      if (window.__kulaError) throw new Error(window.__kulaError);
      return window.__kulaResult;
    };
    chrome.tabs = {
      async query(query) { window.__tabQueries.push(query); return window.__tabResult; },
      async sendMessage(id, message) {
        window.__messages.push({ id, message });
        if (window.__exportError) throw new Error(window.__exportError);
        return window.__exportResult;
      }
    };
    URL.createObjectURL = (blob) => {
      window.__downloadBlob = blob;
      return 'blob:jobright-test-download';
    };
    URL.revokeObjectURL = () => {};
    HTMLAnchorElement.prototype.click = function () {
      window.__downloadCalls.push({ href: this.getAttribute('href'), filename: this.download });
    };
  })()`);
  await page.evaluate(script);
  await page.eventually('document.getElementById("download-job-links").disabled === false');
  return page;
}

test('popup downloads the active tab links as a plain text file with one URL per line', async (t) => {
  const page = await createDownloadPopup(t);
  const links = [`${origin}/jobs/info/one`, `${origin}/jobs/info/two`];
  await page.evaluate(`window.__exportResult = { links: ${JSON.stringify(links)} }; document.getElementById('download-job-links').click()`);
  await page.eventually('window.__downloadCalls.length === 1');
  assert.deepEqual(await page.evaluate('window.__tabQueries'), [{ active: true, currentWindow: true }]);
  assert.deepEqual(await page.evaluate('window.__messages'), [{ id: 42, message: { type: 'GET_JOB_LINKS' } }]);
  assert.deepEqual(await page.evaluate('window.__downloadCalls'), [{ href: 'blob:jobright-test-download', filename: 'jobright-job-links.txt' }]);
  const text = await page.evaluate('window.__downloadBlob.text()');
  assert.deepEqual(text.trimEnd().split('\n'), links, 'Saved text contains the collected links, one per line');
  assert.match(await page.evaluate('window.__downloadBlob.type'), /^text\/plain/);
  assert.match(await page.evaluate('document.getElementById("download-status").textContent'), /2/);
  assert.equal(await page.evaluate('document.getElementById("download-job-links").disabled'), false, 'Button can be used again after download');
});

test('popup reports empty results and messaging failures without downloading an empty file', async (t) => {
  const page = await createDownloadPopup(t);
  await page.evaluate('document.getElementById("download-job-links").click()');
  await page.eventually('/no .*job|no .*link|0 .*job|0 .*link/i.test(document.getElementById("download-status").textContent)');
  assert.equal(await page.evaluate('window.__downloadCalls.length'), 0);
  await page.evaluate('window.__exportError = "Could not establish connection. Receiving end does not exist."; document.getElementById("download-job-links").click()');
  await page.eventually('/refresh|reload/i.test(document.getElementById("download-status").textContent)');
  assert.equal(await page.evaluate('window.__downloadCalls.length'), 0);
  assert.equal(await page.evaluate('document.getElementById("download-job-links").disabled'), false, 'A failed message does not leave the button stuck');
  await page.evaluate('window.__exportError = null; window.__tabResult = []; document.getElementById("download-job-links").click()');
  await page.eventually('/open|jobright|tab/i.test(document.getElementById("download-status").textContent)');
  assert.equal(await page.evaluate('window.__downloadCalls.length'), 0);
});

test('Kula send collects filtered links and sends one replacement batch without downloading a file', async (t) => {
  const page = await createDownloadPopup(t, 'a'.repeat(64));
  const links = [`${origin}/jobs/info/kula-one`, `${origin}/jobs/info/kula-two`];
  await page.evaluate(`window.__exportResult = { links: ${JSON.stringify(links)}, skippedCount: 0, stoppedReason: 'stalled' }; window.__kulaResult = { ok: true, count: 2 }; document.getElementById('send-to-kula-app').click()`);
  await page.eventually('window.__runtimeMessages.length === 1 && !document.getElementById("send-to-kula-app").disabled');
  assert.deepEqual(await page.evaluate('window.__messages'), [{ id: 42, message: { type: 'GET_JOB_LINKS' } }]);
  assert.deepEqual(await page.evaluate('window.__runtimeMessages'), [{ type: 'SEND_TO_KULA', links }]);
  assert.equal(await page.evaluate('window.__downloadCalls.length'), 0, 'Sending to the app does not start a text-file download');
  assert.match(await page.evaluate('document.getElementById("kula-status").textContent'), /2/);
});

test('Kula pairing validates the key and saves it before collection is allowed', async (t) => {
  const page = await createDownloadPopup(t);
  await page.evaluate('document.getElementById("send-to-kula-app").click()');
  await page.eventually('/pair|key|connect/i.test(document.getElementById("kula-status").textContent) && !document.getElementById("send-to-kula-app").disabled');
  assert.equal(await page.evaluate('window.__messages.length'), 0, 'A missing key is reported before the expensive scroll collection');
  assert.equal(await page.evaluate('window.__runtimeMessages.length'), 0);
  await page.evaluate('document.getElementById("kula-pairing-key").value = "invalid-key"; document.getElementById("save-kula-connection").click()');
  await page.eventually('/key|64|valid/i.test(document.getElementById("kula-connection-status").textContent)');
  assert.equal(await page.evaluate('(async () => (await chrome.storage.local.get({ kulaPairingKey: "" })).kulaPairingKey)()'), '');
  const key = 'b'.repeat(64);
  await page.evaluate(`document.getElementById('kula-pairing-key').value = '${key}'; document.getElementById('save-kula-connection').click()`);
  await page.eventually(`(async () => (await chrome.storage.local.get({ kulaPairingKey: '' })).kulaPairingKey === '${key}')()`);
  assert.equal(await page.evaluate('document.getElementById("kula-pairing-key").type'), 'password');
});

test('Kula empty or failed collection never sends a batch that could erase the app list', async (t) => {
  const page = await createDownloadPopup(t, 'a'.repeat(64));
  await page.evaluate('document.getElementById("send-to-kula-app").click()');
  await page.eventually('/no .*job|no .*link|0 .*job|0 .*link/i.test(document.getElementById("kula-status").textContent)');
  assert.equal(await page.evaluate('window.__runtimeMessages.length'), 0, 'Empty collection must preserve the old app list');
  await page.evaluate('window.__exportResult = { error: "The page or filters changed. Download again." }; document.getElementById("send-to-kula-app").click()');
  await page.eventually('/changed/i.test(document.getElementById("kula-status").textContent)');
  assert.equal(await page.evaluate('window.__runtimeMessages.length'), 0, 'A cancelled collection must preserve the old app list');
  assert.equal(await page.evaluate('document.getElementById("send-to-kula-app").disabled'), false);
});

test('Kula reports app failures and mismatched acknowledgements without claiming success', async (t) => {
  const page = await createDownloadPopup(t, 'a'.repeat(64));
  await page.evaluate(`window.__exportResult = { links: ['${origin}/jobs/info/kula-one'] }; window.__kulaResult = { error: 'Open Kula APP and try again.' }; document.getElementById('send-to-kula-app').click()`);
  await page.eventually('/open kula/i.test(document.getElementById("kula-status").textContent) && !document.getElementById("send-to-kula-app").disabled');
  assert.equal(await page.evaluate('document.getElementById("kula-status").classList.contains("error")'), true);
  await page.evaluate('window.__kulaResult = { ok: true, count: 0 }; document.getElementById("send-to-kula-app").click()');
  await page.eventually('window.__runtimeMessages.length === 2 && !document.getElementById("send-to-kula-app").disabled');
  assert.equal(await page.evaluate('document.getElementById("kula-status").classList.contains("error")'), true, 'A different acknowledged count must not be presented as success');
  assert.equal(await page.evaluate('window.__downloadCalls.length'), 0);
});

test('Kula send and download share a collection lock so concurrent clicks cannot mix operations', async (t) => {
  const page = await createDownloadPopup(t, 'a'.repeat(64));
  await page.evaluate(`chrome.tabs.sendMessage = async (id, message) => { window.__messages.push({ id, message }); return new Promise(resolve => { window.__resolveCollection = resolve; }); }; document.getElementById('send-to-kula-app').click()`);
  await page.eventually('typeof window.__resolveCollection === "function"');
  assert.equal(await page.evaluate('document.getElementById("send-to-kula-app").disabled && document.getElementById("download-job-links").disabled'), true);
  await page.evaluate('document.getElementById("download-job-links").click(); document.getElementById("send-to-kula-app").click()');
  assert.equal(await page.evaluate('window.__messages.length'), 1, 'Only one GET_JOB_LINKS request may be active');
  await page.evaluate(`window.__kulaResult = { ok: true, count: 1 }; window.__resolveCollection({ links: ['${origin}/jobs/info/kula-one'] })`);
  await page.eventually('!document.getElementById("send-to-kula-app").disabled && !document.getElementById("download-job-links").disabled');
  assert.equal(await page.evaluate('window.__runtimeMessages.length'), 1);
  assert.equal(await page.evaluate('window.__downloadCalls.length'), 0);
});
