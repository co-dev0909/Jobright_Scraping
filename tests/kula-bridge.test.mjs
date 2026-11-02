import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import { test } from 'node:test';

const workerPath = new URL('../extension/service-worker.js', import.meta.url);
const pairingKey = 'a'.repeat(64); // Synthetic fixture key, never a real app credential.
const links = [
  'https://jobright.ai/jobs/info/111111111111111111111111',
  'https://jobright.ai/jobs/info/222222222222222222222222',
];
const popupSender = { id: 'test-extension', url: 'chrome-extension://test-extension/popup.html' };

async function createWorker({ key = pairingKey, response = { ok: true, count: 2 }, status = 200, fetchError } = {}) {
  const calls = [];
  let listener;
  const context = vm.createContext({
    URL, Headers, Request, Response, TextEncoder, AbortSignal, setTimeout, clearTimeout,
    chrome: {
      runtime: {
        id: 'test-extension',
        getURL: (file) => `chrome-extension://test-extension/${file}`,
        onMessage: { addListener: (handler) => { listener = handler; } },
      },
      storage: { local: { get: async () => ({ kulaPairingKey: key }) } },
    },
    fetch: async (url, options) => {
      calls.push({ url, options });
      if (fetchError) throw fetchError;
      return new Response(JSON.stringify(response), { status, headers: { 'Content-Type': 'application/json' } });
    },
  });
  vm.runInContext(await readFile(workerPath, 'utf8'), context, { filename: 'service-worker.js' });
  assert.equal(typeof listener, 'function', 'The worker registers its runtime message handler');
  return {
    calls,
    send(message = { type: 'SEND_TO_KULA', links }, sender = popupSender) {
      return new Promise((resolve, reject) => {
        const timeout = setTimeout(() => reject(new Error('Worker did not respond')), 2_000);
        const reply = (value) => { clearTimeout(timeout); resolve(value === undefined ? value : JSON.parse(JSON.stringify(value))); };
        try {
          const retained = listener(message, sender, reply);
          if (retained !== true) queueMicrotask(() => reply(undefined));
        } catch (error) {
          clearTimeout(timeout);
          reject(error);
        }
      });
    },
  };
}

test('Kula worker sends one authenticated protocol-versioned batch only to the fixed loopback endpoint', async () => {
  const worker = await createWorker();
  assert.deepEqual(await worker.send(), { ok: true, count: links.length });
  assert.equal(worker.calls.length, 1);
  const { url, options } = worker.calls[0];
  assert.equal(url, 'http://127.0.0.1:43127/v1/job-links');
  assert.equal(options.method, 'POST');
  const headers = new Headers(options.headers);
  assert.equal(headers.get('Authorization'), `Bearer ${pairingKey}`);
  assert.match(headers.get('Content-Type'), /^application\/json/);
  assert.deepEqual(JSON.parse(options.body), { protocolVersion: 1, links });
  assert.ok(!url.includes(pairingKey), 'Pairing key must not appear in URL or query string');
});

test('Kula worker rejects content-script and unrelated senders before reaching the local app', async () => {
  const worker = await createWorker();
  for (const sender of [
    { id: 'test-extension', url: 'https://jobright.ai/jobs/recommend', tab: { id: 42 } },
    { id: 'different-extension', url: popupSender.url },
    { id: 'test-extension', url: 'chrome-extension://test-extension/other.html' },
  ]) {
    const result = await worker.send({ type: 'SEND_TO_KULA', links }, sender);
    assert.notEqual(result?.ok, true);
  }
  assert.equal(worker.calls.length, 0, 'Only the trusted extension popup may replace the app list');
});

test('Kula worker requires a saved pairing key and a nonempty bounded Jobright link batch', async () => {
  const missingKey = await createWorker({ key: '' });
  assert.ok((await missingKey.send()).error);
  assert.equal(missingKey.calls.length, 0);
  const worker = await createWorker();
  for (const invalid of [
    [],
    ['https://example.com/jobs/info/111111111111111111111111'],
    ['http://jobright.ai/jobs/info/111111111111111111111111'],
    ['https://jobright.ai/profile'],
    [null],
    Array.from({ length: 10_001 }, () => links[0]),
  ]) {
    assert.ok((await worker.send({ type: 'SEND_TO_KULA', links: invalid })).error);
  }
  assert.equal(worker.calls.length, 0, 'Invalid batches never reach the replacement endpoint');
});

test('Kula worker distinguishes pairing rejection from an unavailable app', async () => {
  const denied = await createWorker({ status: 401, response: { error: 'Pairing key did not match.' } });
  assert.match((await denied.send()).error, /pair|key/i);
  const unavailable = await createWorker({ fetchError: new TypeError('Failed to fetch') });
  assert.match((await unavailable.send()).error, /open|running|connect|kula|reach/i);
});

test('Kula worker requires a successful acknowledgement with the exact replaced count', async () => {
  for (const options of [
    { response: { ok: true, count: 1 } },
    { response: { ok: false, count: 2 } },
    { response: { ok: true, count: '2' } },
    { response: { error: 'The app could not update its list.' }, status: 500 },
  ]) {
    const worker = await createWorker(options);
    const result = await worker.send();
    assert.ok(result.error, 'An incomplete or failed acknowledgement must not claim the list was replaced');
    assert.notEqual(result.ok, true);
  }
});
