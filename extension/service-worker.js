"use strict";

(() => {
  const KULA_ENDPOINT = "http://127.0.0.1:43127/v1/job-links";
  const MAX_LINKS = 10_000;
  const MAX_BYTES = 1_000_000;

  function canonicalJobLink(link) {
    if (typeof link !== "string" || link.length > 2048) return false;
    try {
      const url = new URL(link);
      return url.origin === "https://jobright.ai" && !url.username && !url.password &&
        !url.search && !url.hash && /^\/jobs\/info\/[^/]+$/.test(url.pathname);
    } catch {
      return false;
    }
  }

  async function sendToKula(links) {
    if (!Array.isArray(links) || links.length === 0 || links.length > MAX_LINKS ||
        links.some((link) => !canonicalJobLink(link)) || new Set(links).size !== links.length) {
      return { error: "The job-link batch is invalid or too large. Collect a smaller filtered list and try again.", code: "invalid_batch" };
    }
    const body = JSON.stringify({ protocolVersion: 1, links });
    if (new TextEncoder().encode(body).length > MAX_BYTES) {
      return { error: "This job-link batch is too large. Use a smaller filtered list and try again.", code: "invalid_batch" };
    }
    const { kulaPairingKey } = await chrome.storage.local.get({ kulaPairingKey: "" });
    if (typeof kulaPairingKey !== "string" || !/^[a-f0-9]{64}$/i.test(kulaPairingKey)) {
      return { error: "Connect to Kula APP first by saving its pairing key in the extension popup.", code: "not_paired" };
    }

    let response;
    try {
      response = await fetch(KULA_ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${kulaPairingKey}` },
        body,
        signal: AbortSignal.timeout(10_000),
        redirect: "error",
        credentials: "omit",
        cache: "no-store",
      });
    } catch {
      return {
        error: "Could not reach Kula APP. Open it on this computer and try again. If a send timed out, check its list before retrying.",
        code: "connection_failed",
      };
    }
    if (response.status === 401 || response.status === 403) {
      return { error: "Kula APP did not accept the pairing key. Copy its key again and save the connection.", code: "pairing_failed" };
    }
    if (!response.ok) {
      return { error: "Kula APP could not accept this batch. Check the app and try again; its previous list was not confirmed replaced.", code: "rejected" };
    }
    let result;
    try {
      result = await response.json();
    } catch {
      return { error: "Kula APP sent an unreadable reply. Check its list before sending again.", code: "invalid_reply" };
    }
    if (result?.ok !== true || result.count !== links.length) {
      return { error: "Kula APP did not confirm this batch. Check its list before sending again.", code: "invalid_reply" };
    }
    return { ok: true, count: result.count };
  }

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message?.type !== "SEND_TO_KULA") return false;
    // Only the extension popup can initiate a replacement. A Jobright content
    // script or a website cannot use this worker as a local-network relay.
    if (sender.id !== chrome.runtime.id || sender.url !== chrome.runtime.getURL("popup.html") || sender.tab) {
      sendResponse({ error: "Open the extension popup to send job links to Kula APP.", code: "invalid_sender" });
      return false;
    }
    void sendToKula(message.links)
      .catch(() => ({ error: "Could not read the Kula connection. Reopen the popup and try again.", code: "connection_failed" }))
      .then(sendResponse);
    return true;
  });
})();
