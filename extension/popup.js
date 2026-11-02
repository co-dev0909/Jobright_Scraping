"use strict";

(() => {
  const toggles = {
    hideApplyNow: document.getElementById("hide-apply-now"),
    hideDuplicates: document.getElementById("hide-duplicates")
  };
  const status = document.getElementById("filter-status");
  const savedPreferences = { hideApplyNow: true, hideDuplicates: true };

  function showStatus(message, error = false) {
    status.textContent = message;
    status.classList.toggle("error", error);
  }

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
