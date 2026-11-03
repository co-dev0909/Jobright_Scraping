"use strict";

importScripts("shared.js");

(() => {
  const ROOT = "http://127.0.0.1:43127";
  const ROLE = "chatgpt";
  let activeCommand = null;
  let loopRunning = false;
  let retryTimer;

  async function config() {
    const values = await chrome.storage.local.get({ automationPairingKey: "", automationClientId: "", automationEnabled: false });
    if (!/^[a-f0-9]{64}$/i.test(values.automationPairingKey) || !values.automationEnabled) throw new Error("Connect this extension to Kula APP first.");
    if (!/^[A-Za-z0-9_-]{16,80}$/.test(values.automationClientId)) {
      values.automationClientId = crypto.randomUUID();
      await chrome.storage.local.set({ automationClientId: values.automationClientId });
    }
    return values;
  }

  async function request(path, body = {}, timeout = 25_000) {
    if (!["/v1/connect", "/v1/poll", "/v1/result", "/v1/authorize"].includes(path)) throw new Error("Unsupported local command endpoint.");
    const settings = await config();
    let response;
    try {
      response = await fetch(`${ROOT}${path}`, {
        method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${settings.automationPairingKey}` },
        body: JSON.stringify({ ...body, role: ROLE, client_id: settings.automationClientId }),
        signal: AbortSignal.timeout(timeout), redirect: "error", credentials: "omit", cache: "no-store",
      });
    } catch { throw new Error("Open Kula APP and its automation connection on this computer."); }
    if (Number(response.headers?.get?.("content-length") || 0) > 16 * 1024 * 1024) throw new Error("The local command was too large.");
    let result;
    try { result = await response.json(); } catch { throw new Error("Kula APP returned an unreadable command response."); }
    if (!response.ok) throw new Error(response.status === 401
      ? "The pairing key was rejected. Copy the automation key from Kula APP again."
      : response.status === 409 ? "This command is stopped, expired, or belongs to another browser."
      : "Kula APP could not accept the connection command.");
    return result;
  }

  async function authorize(command) {
    const result = await request("/v1/authorize", { command_id: command.id, run_id: command.run_id });
    if (result?.ok !== true) throw new Error("Kula APP stopped this command.");
  }

  async function waitForTab(tabId) {
    const deadline = Date.now() + 25_000;
    while (Date.now() < deadline) {
      const tab = await chrome.tabs.get(tabId);
      const url = new URL(tab.url || "https://chatgpt.com/");
      if (url.protocol !== "https:" || url.hostname !== "chatgpt.com") throw new Error("Sign in to ChatGPT in this browser, then restart this job.");
      if (tab.status === "complete") {
        if (url.pathname !== "/") throw new Error("ChatGPT did not open a fresh conversation. Start a new chat and retry this job.");
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
    throw new Error("The ChatGPT automation tab did not finish loading.");
  }

  async function closePreviousOwnedTab(command) {
    const { chatgptOwnedTabId } = await chrome.storage.session.get({ chatgptOwnedTabId: null });
    if (!Number.isInteger(chatgptOwnedTabId)) return;
    try {
      const tab = await chrome.tabs.get(chatgptOwnedTabId);
      if (!tab.url?.startsWith("https://chatgpt.com/")) return;
      const state = await chrome.tabs.sendMessage(chatgptOwnedTabId, { type: "KULA_CHATGPT_DRAFT_STATUS" });
      // Never close a tab containing text the user has begun typing.
      if (state?.hasDraft !== false) return;
      await authorize(command);
      await chrome.tabs.remove(chatgptOwnedTabId);
    } catch { /* A closed tab or a changed page can remain untouched. */ }
  }

  async function executeCommand(command) {
    if (!command || command.action !== "answer_questions" || !/^[A-Za-z0-9_-]{8,80}$/.test(command.id || "") ||
        !/^[A-Za-z0-9_-]{8,80}$/.test(command.run_id || "")) throw new Error("Unsupported ChatGPT command.");
    KulaChatGPT.validatePayload(command.payload);
    if (activeCommand) throw new Error("Another ChatGPT request is still running.");
    const { chatgptClaimedCommands = [] } = await chrome.storage.session.get({ chatgptClaimedCommands: [] });
    if (chatgptClaimedCommands.includes(command.id)) throw new Error("This request was already claimed. It will not be sent twice.");
    await chrome.storage.session.set({ chatgptClaimedCommands: [...chatgptClaimedCommands.slice(-255), command.id] });
    activeCommand = { id: command.id, run_id: command.run_id, tabId: null };
    try {
      await closePreviousOwnedTab(command);
      await authorize(command);
      const tab = await chrome.tabs.create({ url: "https://chatgpt.com/", active: true });
      if (!Number.isInteger(tab.id)) throw new Error("The ChatGPT automation tab could not be opened.");
      activeCommand.tabId = tab.id;
      await chrome.storage.session.set({ chatgptOwnedTabId: tab.id });
      await waitForTab(tab.id);
      await authorize(command);
      await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["shared.js", "content.js"] });
      const reply = await chrome.tabs.sendMessage(tab.id, {
        type: "KULA_CHATGPT_ANSWER", command: { id: command.id, run_id: command.run_id }, payload: command.payload,
      });
      if (!reply?.ok || !reply.result) throw new Error(reply?.error || "ChatGPT could not prepare these answers.");
      // Independently validate the content script's result at the browser boundary.
      return KulaChatGPT.parseResponse(JSON.stringify(reply.result), command.payload);
    } finally { activeCommand = null; }
  }

  async function connect() {
    const result = await request("/v1/connect");
    if (result?.ok !== true) throw new Error("The ChatGPT browser could not register with Kula APP.");
    return result;
  }

  async function pollOnce() {
    if (activeCommand) return;
    await connect();
    const response = await request("/v1/poll");
    const command = response?.command;
    if (!command) return;
    let envelope;
    try {
      const result = await executeCommand(command);
      envelope = { command_id: command.id, run_id: command.run_id, ok: true, result };
    } catch (error) {
      envelope = { command_id: command.id, run_id: command.run_id, ok: false, result: {}, error: error.message || "The ChatGPT request failed." };
    }
    // Retrying only the acknowledgement cannot resend a ChatGPT prompt.
    for (let attempt = 0; attempt < 2; attempt++) {
      try { await request("/v1/result", envelope); return; }
      catch { if (attempt === 1) return; }
    }
  }

  async function loop() {
    if (loopRunning) return;
    loopRunning = true;
    try { await pollOnce(); } catch { /* Offline/waiting is visible from the desktop connection status. */ }
    finally {
      loopRunning = false;
      clearTimeout(retryTimer);
      const { automationEnabled } = await chrome.storage.local.get({ automationEnabled: false });
      if (automationEnabled) retryTimer = setTimeout(() => { void loop(); }, 1500);
    }
  }

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message?.type === "KULA_CHATGPT_AUTHORIZE") {
      if (sender.id !== chrome.runtime.id || !activeCommand || sender.tab?.id !== activeCommand.tabId ||
          message.command_id !== activeCommand.id || message.run_id !== activeCommand.run_id) {
        sendResponse({ ok: false }); return false;
      }
      void authorize(activeCommand).then(() => sendResponse({ ok: true })).catch(() => sendResponse({ ok: false }));
      return true;
    }
    if (!["KULA_CHATGPT_CONNECT", "KULA_CHATGPT_DISCONNECT"].includes(message?.type)) return false;
    if (sender.id !== chrome.runtime.id || sender.url !== chrome.runtime.getURL("popup.html") || sender.tab) {
      sendResponse({ ok: false, error: "Open this extension's popup to configure the connection." }); return false;
    }
    if (message.type === "KULA_CHATGPT_DISCONNECT") {
      void chrome.storage.local.set({ automationEnabled: false }).then(() => {
        clearTimeout(retryTimer); sendResponse({ ok: true });
      });
      return true;
    }
    if (!/^[a-f0-9]{64}$/i.test(message.key || "")) { sendResponse({ ok: false, error: "Invalid pairing key." }); return false; }
    void chrome.storage.local.set({ automationPairingKey: message.key, automationEnabled: true })
      .then(connect).then(() => { sendResponse({ ok: true }); void loop(); })
      .catch((error) => { sendResponse({ ok: false, error: error.message }); void loop(); });
    return true;
  });
  chrome.alarms.create("kula-chatgpt-poll", { periodInMinutes: 0.5 });
  chrome.alarms.onAlarm.addListener((alarm) => { if (alarm.name === "kula-chatgpt-poll") void loop(); });
  chrome.runtime.onStartup.addListener(() => { void loop(); });
  chrome.runtime.onInstalled.addListener(() => { void loop(); });
  globalThis.KulaChatGPTWorker = { executeCommand, request, connect, pollOnce };
  void loop();
})();
