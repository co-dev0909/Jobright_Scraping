import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import readline from 'node:readline';
import vm from 'node:vm';
import { test } from 'node:test';

const appDirectory = fileURLToPath(new URL('../kula-app/', import.meta.url));
const workerFile = new URL('../extension/service-worker.js', import.meta.url);
const testKey = 'a'.repeat(64); // Public fixture key; no real pairing data is read.
const extensionId = 'a'.repeat(32);
const sender = { id: extensionId, url: `chrome-extension://${extensionId}/popup.html` };

async function createWorker(key) {
  let listener;
  const context = vm.createContext({
    URL, Headers, Request, Response, TextEncoder, AbortSignal, setTimeout, clearTimeout,
    chrome: {
      runtime: {
        id: extensionId,
        getURL: (file) => `chrome-extension://${extensionId}/${file}`,
        onMessage: { addListener: (handler) => { listener = handler; } },
      },
      storage: { local: { get: async () => ({ kulaPairingKey: key }) } },
    },
    fetch: (url, options) => {
      // Emulate the browser's extension Origin while keeping the actual HTTP
      // transport, authentication header, endpoint, body, and response intact.
      const headers = new Headers(options.headers);
      headers.set('Origin', `chrome-extension://${extensionId}`);
      return fetch(url, { ...options, headers });
    },
  });
  vm.runInContext(await readFile(workerFile, 'utf8'), context, { filename: 'service-worker.js' });
  assert.equal(typeof listener, 'function');
  return (links) => new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('The extension worker did not respond')), 12_000);
    const respond = (reply) => {
      clearTimeout(timer);
      resolve(JSON.parse(JSON.stringify(reply)));
    };
    try {
      assert.equal(listener({ type: 'SEND_TO_KULA', links }, sender, respond), true);
    } catch (error) {
      clearTimeout(timer);
      reject(error);
    }
  });
}

async function startReceiver() {
  const python = process.env.KULA_TEST_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
  const helper = `
import json
import sys
sys.path.insert(0, sys.argv[1])
from bridge import BridgeServer

def replaced(links):
    print(json.dumps({'event': 'replaced', 'links': links}), flush=True)

server = BridgeServer('a' * 64, replaced, port=43127)
try:
    server.start()
    print(json.dumps({'event': 'ready', 'port': server.port}), flush=True)
    sys.stdin.buffer.read()
finally:
    server.close()
`;
  const child = spawn(python, ['-u', '-c', helper, appDirectory], { stdio: ['pipe', 'pipe', 'pipe'] });
  const messages = [];
  let failure;
  let closed = false;
  let diagnostics = '';
  child.on('error', (error) => { failure = error; });
  child.once('close', () => { closed = true; });
  child.stdin.on('error', () => {}); // Early process failure is reported above.
  child.stderr.on('data', (chunk) => { diagnostics += chunk; });
  const lines = readline.createInterface({ input: child.stdout });
  lines.on('line', (line) => {
    try { messages.push(JSON.parse(line)); }
    catch { failure = new Error('The fixture receiver returned an invalid event'); }
  });

  async function waitFor(predicate) {
    const deadline = Date.now() + 5_000;
    while (Date.now() < deadline) {
      if (failure) throw failure;
      if (predicate()) return;
      if (child.exitCode !== null) throw new Error(`Python receiver exited before readiness: ${diagnostics}`);
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
    throw new Error(`Python receiver did not become ready: ${diagnostics}`);
  }

  async function close() {
    if (closed || !child.pid || child.exitCode !== null || child.signalCode !== null) {
      lines.close();
      return;
    }
    await new Promise((resolve) => {
      const timer = setTimeout(() => child.kill(), 2_000);
      child.once('close', () => { clearTimeout(timer); resolve(); });
      child.stdin.end();
    });
    lines.close();
  }

  try { await waitFor(() => messages.some((message) => message.event === 'ready')); }
  catch (error) { await close(); throw error; }
  return { messages, waitFor, close };
}

test('the actual extension worker sends replacement batches to the Python receiver over HTTP', { timeout: 20_000 }, async () => {
  const receiver = await startReceiver();
  try {
    const send = await createWorker(testKey);
    const first = [
      'https://jobright.ai/jobs/info/111111111111111111111111',
      'https://jobright.ai/jobs/info/222222222222222222222222',
    ];
    const second = ['https://jobright.ai/jobs/info/333333333333333333333333'];
    assert.deepEqual(await send(first), { ok: true, count: 2 });
    assert.deepEqual(await send(second), { ok: true, count: 1 });
    await receiver.waitFor(() => receiver.messages.filter((message) => message.event === 'replaced').length === 2);
    assert.deepEqual(receiver.messages.filter((message) => message.event === 'replaced').map((message) => message.links),
      [first, second], 'The Python receiver gets two complete replacement batches without merging them');

    const wrongKey = await createWorker('b'.repeat(64));
    const denied = await wrongKey(first);
    assert.equal(denied.code, 'pairing_failed');
    assert.equal(receiver.messages.filter((message) => message.event === 'replaced').length, 2,
      'An authenticated HTTP rejection must not replace the Python app list');
  } finally {
    await receiver.close();
  }
});
