"use strict";

(() => {
  const ENDPOINT = "http://127.0.0.1:43127";
  const ROLE = "jobright";
  const ALARM = "kula-application-runner";
  let polling = false;
  let disconnecting = false;
  let activeCommand;
  let activeTab;
  let advanceAuthorized = false;
  let entryAuthorized = false;

  const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
  async function status(message) {
    await chrome.storage.local.set({ automationStatus: message });
  }
  async function config() {
    const values = await chrome.storage.local.get({ automationPairingKey: "", automationClientId: "", automationEnabled: false });
    if (!/^[a-f0-9]{64}$/i.test(values.automationPairingKey) || !values.automationEnabled) return null;
    if (!values.automationClientId) {
      values.automationClientId = crypto.randomUUID();
      await chrome.storage.local.set({ automationClientId: values.automationClientId });
    }
    return { key: values.automationPairingKey, client_id: values.automationClientId };
  }
  async function request(path, body, settings) {
    const response = await fetch(`${ENDPOINT}${path}`, {
      method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${settings.key}` },
      body: JSON.stringify({ role: ROLE, client_id: settings.client_id, ...body }),
      signal: AbortSignal.timeout(25_000), redirect: "error", credentials: "omit", cache: "no-store",
    });
    const length = Number(response.headers.get("Content-Length") || 0);
    if (length > 17_000_000) throw new Error("The automation command is too large.");
    let payload;
    try { payload = await response.json(); }
    catch { throw new Error("Kula APP returned an unreadable reply."); }
    if (!response.ok) {
      const error = new Error(response.status === 401 || response.status === 403
        ? "Kula APP rejected the automation key. Copy it again from the app."
        : payload?.error || "Kula APP did not accept this runner request.");
      error.status = response.status;
      throw error;
    }
    return payload;
  }
  async function authorize(command, settings) {
    if (disconnecting) throw new Error("The application browser was disconnected.");
    const result = await request("/v1/authorize", { command_id: command.id, run_id: command.run_id }, settings);
    if (result?.ok !== true) throw new Error("The application run stopped or lost authorization.");
  }
  function safeApplicationUrl(value, initial = false) {
    try {
      const url = new URL(value);
      const host = url.hostname.replace(/\.$/, "");
      if (url.protocol !== "https:" || url.username || url.password || url.port || host === "localhost" ||
          /\.(?:localhost|local|internal|lan)$/.test(host) || host.includes(":") || /^\d+(?:\.\d+){3}$/.test(host)) return false;
      return !initial || (["jobright.ai", "www.jobright.ai"].includes(url.hostname) && /^\/jobs\/info\/[A-Za-z0-9_-]{1,200}\/?$/.test(url.pathname));
    } catch { return false; }
  }
  async function records() {
    return (await chrome.storage.session.get({ kulaRunnerCommands: {}, kulaApplicationTabs: {} }));
  }
  async function markCommand(command, value) {
    const data = await records();
    data.kulaRunnerCommands[command.id] = { run_id: command.run_id, ...value };
    const keys = Object.keys(data.kulaRunnerCommands);
    for (const id of keys.slice(0, Math.max(0, keys.length - 128))) {
      if (!data.kulaRunnerCommands[id].pending) delete data.kulaRunnerCommands[id];
    }
    await chrome.storage.session.set({ kulaRunnerCommands: data.kulaRunnerCommands });
  }
  async function rememberTab(tabId, command, origin) {
    const data = await records();
    data.kulaApplicationTabs[tabId] = { run_id: command.run_id, origin };
    const ids = Object.keys(data.kulaApplicationTabs);
    for (const id of ids.slice(0, Math.max(0, ids.length - 128))) delete data.kulaApplicationTabs[id];
    await chrome.storage.session.set({ kulaApplicationTabs: data.kulaApplicationTabs });
  }
  async function loadedTab(tabId, timeout = 20_000) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      const tab = await chrome.tabs.get(tabId);
      if (tab.status === "complete" && safeApplicationUrl(tab.url)) return tab;
      if (tab.url && !["about:blank", "about:newtab"].includes(tab.url) && !safeApplicationUrl(tab.url)) {
        throw new Error("The application navigated to an unsupported destination.");
      }
      await delay(250);
    }
    throw new Error("The application page did not finish loading.");
  }
  async function runInTab(tabId, action, payload, command) {
    const tab = await loadedTab(tabId);
    const allowed = (await records()).kulaApplicationTabs[tabId];
    if (!allowed || allowed.run_id !== command.run_id || (allowed.origin && allowed.origin !== new URL(tab.url).origin)) {
      throw new Error("This tab is not an application tab owned by the current Kula run.");
    }
    activeTab = tabId;
    try {
      await chrome.scripting.executeScript({ target: { tabId }, files: ["application-runner.js"] });
      const results = await chrome.scripting.executeScript({ target: { tabId },
        func: (name, data, authorization) => globalThis.KulaApplicationRunner.run(name, data, authorization),
        args: [action, payload, { id: command.id, run_id: command.run_id }],
      });
      if (!results[0] || results[0].frameId !== 0 || !results[0].result) throw new Error("The application runner did not return a main-page result.");
      return results[0].result;
    } finally { activeTab = undefined; }
  }
  function noEntryYet(result) {
    return /^Could not identify a single supported application entry button\.?$/i.test(result?.blocked || "");
  }
  function noFormYet(result) {
    return /^No supported application form was found in the main page\.?$/i.test(result?.blocked || "");
  }
  function sameJobrightDetail(first, second) {
    try {
      const before = new URL(first), after = new URL(second);
      return ["jobright.ai", "www.jobright.ai"].includes(before.hostname) &&
        ["jobright.ai", "www.jobright.ai"].includes(after.hostname) &&
        /^\/jobs\/info\//.test(before.pathname) && before.pathname === after.pathname;
    } catch { return false; }
  }
  async function openEntryWhenReady(tabId, command, settings, popups) {
    const deadline = Date.now() + 15_000;
    while (Date.now() < deadline) {
      await authorize(command, settings);
      popups.clear();
      entryAuthorized = false;
      try {
        const result = await runInTab(tabId, "open_entry", {}, command);
        if (!noEntryYet(result)) return result;
      } catch (error) {
        // The page may navigate immediately after the authorized launch click.
        // Treat it as a single click and observe its outcome; never click again.
        if (entryAuthorized && /context|frame|navigat/i.test(String(error?.message || ""))) return { clicked: true };
        throw error;
      }
      await delay(250);
    }
    return { blocked: "The application entry button did not become available. Wait for the page to finish loading and check its apply button." };
  }
  async function applicationTransition(sourceId, beforeUrl, popups, command, settings) {
    const deadline = Date.now() + 15_000;
    let nextAuthorization = 0;
    let settledKey;
    let settledSince = 0;
    while (Date.now() < deadline) {
      if (Date.now() >= nextAuthorization) {
        await authorize(command, settings);
        nextAuthorization = Date.now() + 1000;
      }
      if (popups.size > 1) return { tab_id: sourceId, blocked: "The apply action opened multiple related tabs. Review them manually." };
      const tabId = popups.size === 1 ? [...popups][0] : sourceId;
      const tab = await chrome.tabs.get(tabId).catch(() => null);
      if (!tab) return { tab_id: sourceId, blocked: "The application tab was closed before its destination could be opened." };
      const blank = !tab.url || ["about:blank", "about:newtab"].includes(tab.url);
      if (!blank && !safeApplicationUrl(tab.url)) return { tab_id: tabId, blocked: "The apply button opened an unsupported application destination." };
      if (tab.status === "complete" && !blank) {
        // Only the source tab's navigation or a correlated browser-created
        // target can become an application tab. Unrelated new tabs are ignored.
        await rememberTab(tabId, command, new URL(tab.url).origin);
        let inspected;
        try { inspected = await runInTab(tabId, "inspect_form", {}, command); }
        catch (error) {
          if (!/context|frame|navigat/i.test(String(error?.message || ""))) throw error;
          await delay(250);
          continue;
        }
        if (popups.size > 1) return { tab_id: sourceId, blocked: "The apply action opened multiple related tabs. Review them manually." };
        if (inspected.complete) return { tab_id: tabId, already_applied: true };
        if (inspected.form_token && !inspected.blocked) return { tab_id: tabId, ready: true, context: inspected.context || "" };
        if (inspected.blocked && !noFormYet(inspected)) return { tab_id: tabId, blocked: inspected.blocked };

        const navigated = tabId !== sourceId || (tab.url !== beforeUrl && !sameJobrightDetail(beforeUrl, tab.url));
        const key = `${tabId}:${tab.url}`;
        if (navigated) {
          if (settledKey !== key) { settledKey = key; settledSince = Date.now(); }
          // An employer may use an intermediate page with its own Apply link.
          // Give hydration/redirects time, then let the outer loop open that
          // explicit entry. Its own readiness wait never repeats a clicked CTA.
          if (Date.now() - settledSince >= 1000) return { tab_id: tabId, transitioned: true };
        } else settledKey = undefined;
      } else settledKey = undefined;
      await delay(250);
    }
    return { tab_id: sourceId, blocked: "The Jobright apply button was clicked, but no application page or dialog opened. Check whether the browser blocked a popup or the site requires attention." };
  }
  async function openApplication(command, settings) {
    const url = command.payload?.url;
    if (!safeApplicationUrl(url, true)) return { blocked: "Only HTTPS Jobright job-detail links can start applications." };
    await authorize(command, settings);
    const tab = await chrome.tabs.create({ url, active: true });
    await rememberTab(tab.id, command, new URL(url).origin);
    let tabId = tab.id;
    for (let step = 0; step < 3; step++) {
      const loaded = await loadedTab(tabId);
      await rememberTab(tabId, command, new URL(loaded.url).origin);
      const sourceId = tabId;
      const popups = new Set();
      const onCreated = (created) => {
        if (created.openerTabId === sourceId && Number.isInteger(created.id)) popups.add(created.id);
      };
      const onNavigationTarget = (details) => {
        if (details.sourceTabId === sourceId && (details.sourceFrameId === undefined || details.sourceFrameId === 0) &&
            Number.isInteger(details.tabId)) popups.add(details.tabId);
      };
      chrome.tabs.onCreated.addListener(onCreated);
      chrome.webNavigation?.onCreatedNavigationTarget?.addListener(onNavigationTarget);
      try {
        const result = await openEntryWhenReady(tabId, command, settings, popups);
        if (result.already_applied) return { tab_id: tabId, already_applied: true };
        if (result.ready) {
          const inspected = await runInTab(tabId, "inspect_form", {}, command);
          return { tab_id: tabId, context: inspected.context || "", blocked: inspected.blocked };
        }
        if (result.blocked) return { tab_id: tabId, blocked: result.blocked };
        if (!result.clicked) return { tab_id: tabId, blocked: "The application entry could not be opened." };
        const transition = await applicationTransition(sourceId, loaded.url, popups, command, settings);
        if (transition.ready || transition.blocked || transition.already_applied) return transition;
        tabId = transition.tab_id;
      } finally {
        chrome.tabs.onCreated.removeListener(onCreated);
        chrome.webNavigation?.onCreatedNavigationTarget?.removeListener(onNavigationTarget);
      }
    }
    return { tab_id: tabId, blocked: "The application needs more manual navigation before it can be filled." };
  }
  async function execute(command, settings) {
    if (!command || typeof command.id !== "string" || typeof command.run_id !== "string" || !command.payload ||
        !["open_application", "inspect_form", "fill_form", "advance"].includes(command.action)) throw new Error("Unsupported automation command.");
    if (command.action === "open_application") return openApplication(command, settings);
    const tabId = command.payload.tab_id;
    if (!Number.isInteger(tabId)) throw new Error("An application tab is required.");
    const before = await chrome.tabs.get(tabId);
    advanceAuthorized = false;
    try { return await runInTab(tabId, command.action, command.payload, command); }
    catch (error) {
      // A normal full-page navigation destroys an executeScript promise. Only
      // inspect the resulting owned page; never retry the Next/Submit click.
      if (command.action !== "advance" || !advanceAuthorized ||
          !/context|frame|navigat/i.test(String(error?.message || ""))) throw error;
      const after = await loadedTab(tabId);
      if (!safeApplicationUrl(after.url) || (after.url === before.url && after.status !== "complete")) throw error;
      await authorize(command, settings);
      await rememberTab(tabId, command, new URL(after.url).origin);
      const state = await runInTab(tabId, "inspect_form", {}, command);
      if (state.complete) return { complete: true, submitted: true };
      if (command.payload.mode === "submit") return { submitted: false,
        blocked: "The submission navigated but no success confirmation was found. Check the application manually; it will not be clicked again." };
      return state.blocked ? state : { complete: false };
    }
  }
  async function postResult(command, record, settings) {
    await request("/v1/result", { command_id: command.id, run_id: command.run_id,
      ok: record.ok, result: record.result || {}, ...(record.error ? { error: record.error } : {}) }, settings);
    await markCommand(command, { ...record, pending: false });
  }
  async function processCommand(command, settings) {
    const existing = (await records()).kulaRunnerCommands[command.id];
    if (existing) {
      const result = existing.state === "completed" ? existing : {
        state: "completed", pending: true, ok: false, result: {},
        error: "This command was already claimed before a runner restart. Check the browser; it will not be replayed.",
      };
      await postResult(command, result, settings);
      return;
    }
    await markCommand(command, { state: "claimed", pending: true });
    activeCommand = command;
    let record;
    try {
      const result = await execute(command, settings);
      record = { state: "completed", pending: true, ok: true, result };
    } catch (error) {
      record = { state: "completed", pending: true, ok: false, result: {},
        error: String(error?.message || "The application command failed.").slice(0, 600) };
    } finally { activeCommand = undefined; activeTab = undefined; }
    await markCommand(command, record);
    await postResult(command, record, settings);
  }
  async function flushResults(settings) {
    const data = await records();
    for (const [id, record] of Object.entries(data.kulaRunnerCommands)) {
      if (!record.pending) continue;
      const command = { id, run_id: record.run_id };
      const completed = record.state === "completed" ? record : { state: "completed", pending: true, ok: false, result: {},
        error: "This command was interrupted. Inspect the application before attempting it again." };
      try { await postResult(command, completed, settings); }
      catch (error) {
        // A canceled/expired result is terminal, while transport failures keep
        // its result pending for an idempotent retry. Neither replays a click.
        if (![400, 404, 409].includes(error?.status)) throw error;
        await markCommand(command, { ...completed, pending: false });
      }
    }
  }
  async function poll() {
    if (polling) return;
    polling = true;
    disconnecting = false;
    const deadline = Date.now() + 240_000;
    try {
      while (!disconnecting && Date.now() < deadline) {
        const settings = await config();
        if (!settings) break;
        try {
          await request("/v1/connect", {}, settings);
          await flushResults(settings);
          await status("Application browser connected. Start jobs in Kula APP.");
          const reply = await request("/v1/poll", {}, settings);
          if (reply.command) await processCommand(reply.command, settings);
        } catch {
          await status("Waiting for Kula APP. Open its automation connection and check the key.");
          await delay(3000);
        }
      }
    } finally { polling = false; }
  }
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (sender.id !== chrome.runtime.id) return false;
    if (message?.type === "KULA_AUTHORIZE") {
      if (!activeCommand || activeTab !== sender.tab?.id || message.command_id !== activeCommand.id || message.run_id !== activeCommand.run_id) {
        sendResponse({ ok: false }); return false;
      }
      void config().then((settings) => settings ? authorize(activeCommand, settings) : Promise.reject())
        .then(() => {
          if (activeCommand?.action === "advance") advanceAuthorized = true;
          if (activeCommand?.action === "open_application") entryAuthorized = true;
          sendResponse({ ok: true });
        }, () => sendResponse({ ok: false }));
      return true;
    }
    if (sender.tab || sender.url !== chrome.runtime.getURL("popup.html")) return false;
    if (message?.type === "KULA_DISCONNECT") {
      disconnecting = true;
      void chrome.storage.local.set({ automationEnabled: false, automationStatus: "Runner disconnected." })
        .then(() => sendResponse({ ok: true }));
      return true;
    }
    if (message?.type !== "KULA_CONNECT") return false;
    void config().then(async (settings) => {
      if (!settings) return { ok: false, message: "Save the automation key first." };
      await chrome.alarms.create(ALARM, { periodInMinutes: 0.5 });
      try {
        await request("/v1/connect", {}, settings);
        void poll();
        return { ok: true, message: "Application browser connected. Start jobs in Kula APP." };
      } catch {
        void poll();
        return { ok: false, message: "Waiting for Kula APP. Open its automation connection and check the key." };
      }
    }).then(sendResponse, () => sendResponse({ ok: false, message: "Could not initialize the application runner." }));
    return true;
  });
  chrome.alarms.onAlarm.addListener((alarm) => { if (alarm.name === ALARM) void poll(); });
  chrome.runtime.onStartup.addListener(() => { void poll(); });
  chrome.runtime.onInstalled.addListener(() => { void chrome.alarms.create(ALARM, { periodInMinutes: 0.5 }); void poll(); });
})();
