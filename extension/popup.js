"use strict";

(() => {
  const toggles = {
    hideApplyNow: document.getElementById("hide-apply-now"),
    hideDuplicates: document.getElementById("hide-duplicates")
  };
  const status = document.getElementById("filter-status");
  const downloadButton = document.getElementById("download-job-links");
  const downloadStatus = document.getElementById("download-status");
  const sendButton = document.getElementById("send-to-kula-app");
  const kulaStatus = document.getElementById("kula-status");
  const pairingKeyInput = document.getElementById("kula-pairing-key");
  const saveConnectionButton = document.getElementById("save-kula-connection");
  const connectionStatus = document.getElementById("kula-connection-status");
  const validPairingKey = (value) => typeof value === "string" && /^[a-f0-9]{64}$/i.test(value);
  let collecting = false;
  const savedPreferences = { hideApplyNow: true, hideDuplicates: true };

  function showStatus(message, error = false) {
    status.textContent = message;
    status.classList.toggle("error", error);
  }

  function showActionStatus(element, message, error = false) {
    element.textContent = message;
    element.classList.toggle("error", error);
  }

  function setCollecting(active, button) {
    collecting = active;
    downloadButton.disabled = active;
    sendButton.disabled = active;
    saveConnectionButton.disabled = active;
    if (active) button.setAttribute("aria-busy", "true");
    else button.removeAttribute("aria-busy");
  }

  function collectionNote(result) {
    const stopped = result.stoppedReason === "limit"
      ? "Collection limit reached; this batch contains the links collected so far."
      : "Stopped when no more jobs loaded; Jobright may have more results.";
    const skipped = result.skippedCount > 0
      ? ` ${result.skippedCount} visible card${result.skippedCount === 1 ? " was" : "s were"} skipped because a job link could not be identified.`
      : "";
    return `${stopped}${skipped}`;
  }

  async function collectLinks() {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!Number.isInteger(tab?.id)) {
      throw new Error("Open your filtered Jobright jobs page, then try again.");
    }
    let result;
    try {
      result = await chrome.tabs.sendMessage(tab.id, { type: "GET_JOB_LINKS" });
    } catch {
      throw new Error("Could not collect job links. Open a Jobright jobs page, refresh it, and try again.");
    }
    if (result?.error) throw new Error(result.error);
    if (!Array.isArray(result?.links) || result.links.some((link) => typeof link !== "string")) {
      throw new Error("Could not read job links. Refresh Jobright and try again.");
    }
    if (result.links.length === 0) {
      const skipped = result.skippedCount > 0
        ? ` ${result.skippedCount} visible card${result.skippedCount === 1 ? " had" : "s had"} no identifiable job link.`
        : "";
      const limit = result.stoppedReason === "limit" ? " Collection limit reached." : "";
      throw new Error(`No downloadable job links found.${skipped}${limit} Check your Jobright results and filters, then try again.`);
    }
    return result;
  }

  function downloadLinks(links) {
    const blob = new Blob([`${links.join("\n")}\n`], { type: "text/plain;charset=utf-8" });
    const objectUrl = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = objectUrl;
    anchor.download = "jobright-job-links.txt";
    document.body.append(anchor);
    try {
      anchor.click();
    } finally {
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(objectUrl), 30_000);
    }
  }

  async function runCollection(target) {
    if (collecting) return;
    const sending = target === "kula";
    const button = sending ? sendButton : downloadButton;
    const output = sending ? kulaStatus : downloadStatus;
    setCollecting(true, button);
    try {
      if (sending) {
        const { kulaPairingKey } = await chrome.storage.local.get({ kulaPairingKey: "" });
        if (!validPairingKey(kulaPairingKey)) {
          document.querySelector(".kula-connection").open = true;
          throw new Error("Connect to Kula APP first: copy its pairing key, paste it below, and save the connection.");
        }
      }
      showActionStatus(output, "Scrolling and collecting job links… Keep this popup open and leave your filters unchanged.");
      const result = await collectLinks();
      if (sending) {
        showActionStatus(output, "Sending job links to Kula APP… Keep this popup open.");
        let reply;
        try {
          reply = await chrome.runtime.sendMessage({ type: "SEND_TO_KULA", links: result.links });
        } catch {
          throw new Error("Could not reach the Kula connection. Reload the extension and try again.");
        }
        if (reply?.error) throw new Error(reply.error);
        if (reply?.ok !== true || reply.count !== result.links.length) {
          throw new Error("Kula APP did not confirm this batch. Check its list before sending again.");
        }
        showActionStatus(output, `Kula APP list replaced with ${reply.count} job link${reply.count === 1 ? "" : "s"}. ${collectionNote(result)}`);
      } else {
        downloadLinks(result.links);
        const count = result.links.length;
        showActionStatus(output, `Download started: ${count} job link${count === 1 ? "" : "s"}. ${collectionNote(result)}`);
      }
    } catch (error) {
      showActionStatus(output, error instanceof Error ? error.message : "Could not collect job links. Please try again.", true);
    } finally {
      setCollecting(false, button);
    }
  }

  downloadButton.addEventListener("click", () => { void runCollection("download"); });
  sendButton.addEventListener("click", () => { void runCollection("kula"); });

  saveConnectionButton.addEventListener("click", async () => {
    const key = pairingKeyInput.value.trim();
    if (!validPairingKey(key)) {
      showActionStatus(connectionStatus, "Paste the full 64-character pairing key copied from Kula APP.", true);
      return;
    }
    saveConnectionButton.disabled = true;
    try {
      await chrome.storage.local.set({ kulaPairingKey: key });
      pairingKeyInput.value = "";
      showActionStatus(connectionStatus, "Pairing key saved. Keep Kula APP open, then send your job links.");
    } catch {
      showActionStatus(connectionStatus, "Could not save the pairing key. Please try again.", true);
    } finally {
      saveConnectionButton.disabled = collecting;
    }
  });

  function showPreferences(values) {
    for (const [key, toggle] of Object.entries(toggles)) {
      if (Object.hasOwn(values, key)) {
        savedPreferences[key] = values[key] !== false;
        toggle.checked = savedPreferences[key];
      }
    }
    const applyStatus = savedPreferences.hideApplyNow ? "on" : "off";
    const duplicateStatus = savedPreferences.hideDuplicates ? "on" : "off";
    showStatus(`APPLY NOW filter ${applyStatus}. Duplicate filter ${duplicateStatus}.`);
  }

  for (const [key, toggle] of Object.entries(toggles)) {
    toggle.addEventListener("change", async () => {
      const requestedPreference = toggle.checked;
      toggle.disabled = true;
      showStatus("Saving your preference...");

      try {
        await chrome.storage.local.set({ [key]: requestedPreference });
        showPreferences({ [key]: requestedPreference });
      } catch {
        toggle.checked = savedPreferences[key];
        showStatus("Could not save your choice. Please try again.", true);
      } finally {
        toggle.disabled = false;
      }
    });
  }

  async function initialize() {
    try {
      const settings = await chrome.storage.local.get({ hideApplyNow: true, hideDuplicates: true, kulaPairingKey: "" });
      showPreferences(settings);
      showActionStatus(connectionStatus, validPairingKey(settings.kulaPairingKey)
        ? "Pairing key saved. Keep Kula APP open when sending."
        : "Not connected yet. Copy the pairing key from Kula APP to connect.");
      for (const toggle of Object.values(toggles)) {
        toggle.disabled = false;
      }

      chrome.storage.onChanged.addListener((changes, areaName) => {
        if (areaName !== "local") return;
        const values = {};
        for (const key of Object.keys(toggles)) {
          if (Object.hasOwn(changes, key)) {
            values[key] = changes[key].newValue;
          }
        }
        if (Object.keys(values).length > 0) {
          showPreferences(values);
        }
      });
    } catch {
      showStatus("Could not load your preferences. Close and reopen this popup to retry.", true);
    }
  }

  void initialize();
})();
