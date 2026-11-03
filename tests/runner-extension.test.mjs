import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import { test } from 'node:test';

const extensionId = 'a'.repeat(32);
async function worker({ command, claimed = false, authorized = true, navigation = false } = {}) {
  const source = await readFile(new URL('../extension/runner-worker.js', import.meta.url), 'utf8');
  const local = { automationEnabled: true, automationPairingKey: 'a'.repeat(64), automationClientId: 'fixture-client' };
  const session = { kulaRunnerCommands: claimed ? { [command.id]: { run_id: command.run_id, state: 'claimed', pending: false } } : {},
    kulaApplicationTabs: navigation ? { 7: { run_id: command.run_id, origin: 'https://jobright.ai' } } : {} };
  const requests = [], created = [];
  let currentUrl = 'https://jobright.ai/jobs/info/job';
  let advanceClicks = 0;
  let listener;
  const storage = (state) => ({ get: async (defaults) => ({ ...structuredClone(defaults), ...structuredClone(state) }), set: async (values) => Object.assign(state, structuredClone(values)) });
  const event = () => ({ addListener() {}, removeListener() {} });
  const context = vm.createContext({ URL, AbortSignal, Headers, crypto, setTimeout, clearTimeout, Date,
    fetch: async (url, options) => {
      const body = JSON.parse(options.body);
      requests.push({ url, body, headers: options.headers });
      if (url.endsWith('/v1/poll')) { if (!navigation) local.automationEnabled = false; return Response.json({ command }); }
      if (url.endsWith('/v1/result')) local.automationEnabled = false;
      return Response.json({ ok: url.endsWith('/v1/authorize') ? authorized : true });
    },
    chrome: { storage: { local: storage(local), session: storage(session) },
      runtime: { id: extensionId, getURL: (file) => `chrome-extension://${extensionId}/${file}`,
        onMessage: { addListener: (handler) => { listener = handler; } }, onStartup: event(), onInstalled: event() },
      alarms: { create: async () => {}, onAlarm: event() },
      tabs: { create: async (tab) => { created.push(tab); throw new Error('Fixture must not open a real browser'); },
        get: async () => ({ id: 7, url: currentUrl, status: 'complete' }), onCreated: event() },
      scripting: { executeScript: async (options) => {
        if (options.files) return [{ frameId: 0 }];
        if (options.args[0] === 'advance') {
          const reply = await new Promise((resolve) => listener({ type: 'KULA_AUTHORIZE', command_id: command.id, run_id: command.run_id },
            { id: extensionId, tab: { id: 7 } }, resolve));
          assert.equal(reply.ok, true);
          advanceClicks++;
          currentUrl = 'https://jobright.ai/application/thank-you';
          throw new Error('Execution context was destroyed by navigation');
        }
        return [{ frameId: 0, result: { complete: true, fields: [], next_action: 'none' } }];
      } },
    },
  });
  vm.runInContext(source, context);
  const send = (message, sender = { id: extensionId, url: `chrome-extension://${extensionId}/popup.html` }) => new Promise((resolve) => {
    if (!listener(message, sender, (response) => resolve(JSON.parse(JSON.stringify(response))))) resolve(null);
  });
  return { send, requests, created, session, advanceClicks: () => advanceClicks };
}
async function resultOf(fixture) {
  const deadline = Date.now() + 1000;
  while (Date.now() < deadline) {
    const result = fixture.requests.find((request) => request.url.endsWith('/v1/result'));
    if (result) return result.body;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  assert.fail('Worker did not return its command result');
}

test('only the own popup can configure the application runner', async () => {
  const fixture = await worker();
  assert.equal(await fixture.send({ type: 'KULA_CONNECT' }, { id: extensionId, tab: { id: 1 }, url: 'https://jobright.ai/' }), null);
  assert.equal(fixture.requests.length, 0);
});

test('invalid or private start URLs never create an application tab', async () => {
  for (const url of ['https://127.0.0.1/jobs/info/job', 'javascript:alert(1)', 'https://other.example/jobs/info/job']) {
    const fixture = await worker({ command: { id: 'command', run_id: 'run', action: 'open_application', payload: { url } } });
    await fixture.send({ type: 'KULA_CONNECT' });
    assert.match((await resultOf(fixture)).result.blocked, /Jobright/i);
    assert.deepEqual(fixture.created, []);
  }
});

test('revoked run authorization prevents opening the job tab', async () => {
  const fixture = await worker({ authorized: false, command: { id: 'command', run_id: 'run', action: 'open_application', payload: { url: 'https://jobright.ai/jobs/info/job' } } });
  await fixture.send({ type: 'KULA_CONNECT' });
  const result = await resultOf(fixture);
  assert.equal(result.ok, false);
  assert.deepEqual(fixture.created, []);
  assert.equal(fixture.requests.find((request) => request.url.endsWith('/v1/authorize')).headers.Authorization, `Bearer ${'a'.repeat(64)}`);
});

test('a command claimed before worker restart is not replayed', async () => {
  const fixture = await worker({ claimed: true, command: { id: 'command', run_id: 'run', action: 'open_application', payload: { url: 'https://jobright.ai/jobs/info/job' } } });
  await fixture.send({ type: 'KULA_CONNECT' });
  const result = await resultOf(fixture);
  assert.equal(result.ok, false);
  assert.match(result.error, /not be replayed/i);
  assert.deepEqual(fixture.created, []);
});

test('a full-page submit navigation is inspected for confirmation without clicking again', async () => {
  const fixture = await worker({ navigation: true, command: { id: 'command', run_id: 'run', action: 'advance',
    payload: { tab_id: 7, form_token: 'form', mode: 'submit' } } });
  await fixture.send({ type: 'KULA_CONNECT' });
  const result = await resultOf(fixture);
  assert.equal(result.ok, true);
  assert.deepEqual(result.result, { complete: true, submitted: true });
  assert.equal(fixture.advanceClicks(), 1, 'Recovery must never repeat the final submit click');
  assert.equal(fixture.requests.filter((request) => request.url.endsWith('/v1/authorize')).length, 2,
    'The post-navigation inspection rechecks that the run is active');
});

async function launchWorker(scenario) {
  const source = await readFile(new URL('../extension/runner-worker.js', import.meta.url), 'utf8');
  const command = { id: 'launch-command', run_id: 'launch-run', action: 'open_application',
    payload: { url: 'https://jobright.ai/jobs/info/fixture-job' } };
  const local = { automationEnabled: true, automationPairingKey: 'a'.repeat(64), automationClientId: 'fixture-client' };
  const session = { kulaRunnerCommands: {}, kulaApplicationTabs: {} };
  const storage = (state) => ({ get: async (defaults) => ({ ...structuredClone(defaults), ...structuredClone(state) }),
    set: async (values) => Object.assign(state, structuredClone(values)) });
  const event = () => {
    const listeners = new Set();
    return { addListener: (listener) => listeners.add(listener), removeListener: (listener) => listeners.delete(listener),
      emit: (value) => { for (const listener of [...listeners]) listener(value); }, listeners };
  };
  const createdEvent = event(), navigationEvent = event();
  const requests = [], injected = [], clicks = [];
  const tabs = new Map([[7, { id: 7, url: command.payload.url, status: 'complete' }]]);
  let now = 0, clickedAt = null, popupCreated = false, unrelatedCreated = false, listener;
  class Clock extends Date { static now() { return now; } }

  function tick() {
    if (clickedAt === null) return;
    const elapsed = now - clickedAt;
    if ((scenario === 'unrelated' || scenario === 'unrelated-only') && !unrelatedCreated && elapsed >= 750) {
      unrelatedCreated = true;
      tabs.set(99, { id: 99, url: 'https://unrelated.example/form', status: 'complete' });
      createdEvent.emit(tabs.get(99));
      navigationEvent.emit({ sourceTabId: 900, sourceFrameId: 0, tabId: 99 });
    }
    if (['popup', 'noopener', 'redirect', 'multiple', 'private'].includes(scenario) && !popupCreated && elapsed >= 1500) {
      popupCreated = true;
      const created = { id: 8, url: 'about:blank', status: 'loading', ...(scenario === 'noopener' ? {} : { openerTabId: 7 }) };
      tabs.set(8, created);
      createdEvent.emit(created);
      if (scenario === 'noopener') navigationEvent.emit({ sourceTabId: 7, sourceFrameId: 0, tabId: 8 });
      if (scenario === 'multiple') {
        tabs.set(9, { id: 9, url: 'https://second.example/apply', status: 'complete' });
        navigationEvent.emit({ sourceTabId: 7, sourceFrameId: 0, tabId: 9 });
      }
    }
    if (tabs.has(8) && elapsed >= 2000) {
      tabs.set(8, { ...tabs.get(8), url: scenario === 'private' ? 'https://127.0.0.1/private'
        : scenario === 'redirect' && elapsed < 2750 ? 'https://tracking.example/redirect' : 'https://jobs.employer.example/apply',
      status: elapsed >= 2500 ? 'complete' : 'loading' });
    }
    if (scenario === 'same-tab' && elapsed >= 1500) {
      tabs.set(7, { id: 7, url: 'https://jobs.employer.example/apply', status: elapsed >= 2500 ? 'complete' : 'loading' });
    }
  }
  const context = vm.createContext({ URL, AbortSignal, Headers, crypto, Date: Clock,
    setTimeout: (callback, milliseconds) => setImmediate(() => { now += milliseconds; tick(); callback(); }),
    clearTimeout: clearImmediate,
    fetch: async (url, options) => {
      requests.push({ url, body: JSON.parse(options.body), headers: options.headers });
      if (url.endsWith('/v1/poll')) return Response.json({ command });
      if (url.endsWith('/v1/result')) local.automationEnabled = false;
      return Response.json({ ok: true });
    },
    chrome: { storage: { local: storage(local), session: storage(session) },
      runtime: { id: extensionId, getURL: (file) => `chrome-extension://${extensionId}/${file}`,
        onMessage: { addListener: (handler) => { listener = handler; } }, onStartup: event(), onInstalled: event() },
      alarms: { create: async () => {}, onAlarm: event() },
      tabs: { create: async (tab) => ({ ...tab, id: 7, status: 'complete' }),
        get: async (id) => { tick(); if (!tabs.has(id)) throw new Error('Missing fixture tab'); return { ...tabs.get(id) }; },
        onCreated: createdEvent },
      webNavigation: { onCreatedNavigationTarget: navigationEvent },
      scripting: { executeScript: async (options) => {
        injected.push(options.target.tabId);
        if (options.files) return [{ frameId: 0 }];
        const action = options.args[0];
        if (action === 'open_entry') {
          if (scenario === 'hydrating' && now < 1750) return [{ frameId: 0,
            result: { blocked: 'Could not identify a single supported application entry button.' } }];
          assert.equal(clickedAt, null, 'The launch control must never be clicked a second time');
          const authorization = await new Promise((resolve) => listener({ type: 'KULA_AUTHORIZE',
            command_id: command.id, run_id: command.run_id }, { id: extensionId, tab: { id: 7 } }, resolve));
          assert.equal(authorization.ok, true);
          clickedAt = now;
          clicks.push({ tabId: options.target.tabId, time: now });
          return [{ frameId: 0, result: { clicked: true } }];
        }
        assert.equal(action, 'inspect_form');
        const elapsed = now - clickedAt;
        const ready = ['dialog', 'unrelated', 'hydrating'].includes(scenario) ? elapsed >= 2500
          : ['popup', 'noopener', 'redirect'].includes(scenario) ? options.target.tabId === 8 && elapsed >= 3000
          : scenario === 'same-tab' && elapsed >= 3000;
        return [{ frameId: 0, result: ready ? { form_token: 'employer-form', fields: [], context: 'Application fixture' }
          : { blocked: 'No supported application form was found in the main page.' } }];
      } },
    },
  });
  vm.runInContext(source, context);
  await new Promise((resolve) => listener({ type: 'KULA_CONNECT' },
    { id: extensionId, url: `chrome-extension://${extensionId}/popup.html` }, resolve));
  return { requests, clicks, injected, session, createdEvent, navigationEvent, elapsed: () => now };
}

test('a delayed Jobright popup is followed through its blank page and asynchronous employer form', async () => {
  const fixture = await launchWorker('popup');
  const reply = await resultOf(fixture);
  assert.equal(reply.ok, true);
  assert.equal(reply.result.tab_id, 8);
  assert.equal(reply.result.ready, true);
  assert.equal(fixture.clicks.length, 1);
  assert.ok(fixture.elapsed() >= 3000, 'The runner waits past the old 700ms popup window and for the employer form');
  assert.equal(fixture.session.kulaApplicationTabs[8].origin, 'https://jobs.employer.example');
  assert.equal(fixture.createdEvent.listeners.size, 0);
  assert.equal(fixture.navigationEvent.listeners.size, 0);
});

test('a delayed same-tab application navigation and an in-page dialog each need only one launch click', async () => {
  for (const scenario of ['same-tab', 'dialog', 'hydrating']) {
    const fixture = await launchWorker(scenario);
    const reply = await resultOf(fixture);
    assert.equal(reply.result.tab_id, 7);
    assert.equal(reply.result.ready, true);
    assert.equal(fixture.clicks.length, 1, scenario);
  }
});

test('browser navigation-target correlation follows noopener application tabs and employer redirects', async () => {
  for (const scenario of ['noopener', 'redirect']) {
    const fixture = await launchWorker(scenario);
    const reply = await resultOf(fixture);
    assert.equal(reply.result.tab_id, 8);
    assert.equal(reply.result.ready, true);
    assert.equal(fixture.clicks.length, 1);
    assert.equal(fixture.session.kulaApplicationTabs[8].origin, 'https://jobs.employer.example');
  }
});

test('unrelated new tabs cannot become the job application destination', async () => {
  const fixture = await launchWorker('unrelated');
  const reply = await resultOf(fixture);
  assert.equal(reply.result.tab_id, 7);
  assert.equal(reply.result.ready, true);
  assert.equal(fixture.injected.includes(99), false);
  assert.equal(fixture.session.kulaApplicationTabs[99], undefined);
});

test('a launch with no application transition reports the clicked-button failure without retrying', async () => {
  for (const scenario of ['missing', 'unrelated-only']) {
    const fixture = await launchWorker(scenario);
    const reply = await resultOf(fixture);
    assert.equal(reply.ok, true);
    assert.match(reply.result.blocked, /apply button was clicked.*no application page or dialog opened/i);
    assert.equal(fixture.clicks.length, 1);
    assert.ok(fixture.elapsed() >= 15000);
    assert.equal(fixture.injected.includes(99), false);
    assert.equal(fixture.createdEvent.listeners.size, 0);
    assert.equal(fixture.navigationEvent.listeners.size, 0);
  }
});

test('multiple correlated popups and private destinations are blocked without choosing another tab', async () => {
  const multiple = await resultOf(await launchWorker('multiple'));
  assert.match(multiple.result.blocked, /multiple related tabs/i);
  const blocked = await resultOf(await launchWorker('private'));
  assert.match(blocked.result.blocked, /unsupported application destination/i);
});
