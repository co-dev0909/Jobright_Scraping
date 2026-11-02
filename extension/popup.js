"use strict";

(() => {
  const toggles = {
    hideApplyNow: document.getElementById("hide-apply-now"),
    hideDuplicates: document.getElementById("hide-duplicates")
  };
  const status = document.getElementById("filter-status");
  const downloadButton = document.getElementById("download-job-links");
  const downloadStatus = document.getElementById("download-status");
  const savedPreferences = { hideApplyNow: true, hideDuplicates: true };

  function showStatus(message, error = false) {
    status.textContent = message;
    status.classList.toggle("error", error);
  }

  function showDownloadStatus(message, error = false) {
    downloadStatus.textContent = message;
    downloadStatus.classList.toggle("error", error);
  }

  downloadButton.addEventListener("click", async () => {
    downloadButton.disabled = true;
    downloadButton.setAttribute("aria-busy", "true");
    showDownloadStatus("Scrolling and collecting job links… Keep this popup open and leave your filters unchanged.");

    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!Number.isInteger(tab?.id)) {
        showDownloadStatus("Open your filtered Jobright jobs page, then try again.", true);
        return;
      }

      const result = await chrome.tabs.sendMessage(tab.id, { type: "GET_JOB_LINKS" });
      if (result?.error) {
        showDownloadStatus(result.error, true);
        return;
      }
      if (!Array.isArray(result?.links) || result.links.some((link) => typeof link !== "string")) {
        throw new Error("Invalid job link response");
      }
      if (result.links.length === 0) {
        const skipped = result.skippedCount > 0
          ? ` ${result.skippedCount} visible card${result.skippedCount === 1 ? " had" : "s had"} no identifiable job link.`
          : "";
        const limit = result.stoppedReason === "limit" ? " Collection limit reached." : "";
        showDownloadStatus(`No downloadable job links found.${skipped}${limit} Check your Jobright results and filters, then try again.`);
        return;
      }

      const blob = new Blob([`${result.links.join("\n")}\n`], { type: "text/plain;charset=utf-8" });
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = "jobright-job-links.txt";
      document.body.append(anchor);
      try {
        anchor.click();
      } finally {
        anchor.remove();
        // Allow the browser to begin reading the file before releasing it.
        setTimeout(() => URL.revokeObjectURL(objectUrl), 30_000);
      }

      const count = result.links.length;
      const stopped = result.stoppedReason === "limit"
        ? "Time limit reached; this file contains the links collected so far."
        : "Stopped when no more jobs loaded; Jobright may have more results.";
      const skipped = result.skippedCount > 0
        ? ` ${result.skippedCount} visible card${result.skippedCount === 1 ? " was" : "s were"} skipped because a job link could not be identified.`
        : "";
      showDownloadStatus(`Download started: ${count} job link${count === 1 ? "" : "s"}. ${stopped}${skipped}`);
    } catch {
      showDownloadStatus("Could not collect job links. Open a Jobright jobs page, refresh it, and try again.", true);
    } finally {
      downloadButton.disabled = false;
      downloadButton.removeAttribute("aria-busy");
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
      const settings = await chrome.storage.local.get({ hideApplyNow: true, hideDuplicates: true });
      showPreferences(settings);
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
