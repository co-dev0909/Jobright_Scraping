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
