"use strict";

(() => {
  const key = document.getElementById("runner-key");
  const connect = document.getElementById("runner-connect");
  const disconnect = document.getElementById("runner-disconnect");
  const status = document.getElementById("runner-status");
  function report(message, error = false) {
    status.textContent = message;
    status.classList.toggle("error", error);
  }
  connect.addEventListener("click", async () => {
    const pairingKey = key.value.trim();
    if (!/^[a-f0-9]{64}$/i.test(pairingKey)) {
      report("Paste the full automation key copied from Kula APP.", true);
      return;
    }
    connect.disabled = true;
    try {
      // This request must run directly from the user's Connect click.
      const granted = await chrome.permissions.request({ origins: ["https://*/*"] });
      if (!granted) throw new Error("Application-page access was not granted. The download feature still works.");
      await chrome.storage.local.set({ automationPairingKey: pairingKey, automationEnabled: true });
      key.value = "";
      const result = await chrome.runtime.sendMessage({ type: "KULA_CONNECT" });
      report(result?.message || "Waiting for Kula APP's automation connection.", !result?.ok);
    } catch (error) {
      report(error?.message || "Could not connect the application runner.", true);
    } finally { connect.disabled = false; }
  });
  disconnect.addEventListener("click", async () => {
    try {
      await chrome.runtime.sendMessage({ type: "KULA_DISCONNECT" });
      report("Runner disconnected. Download and filtering remain available.");
    } catch { report("Could not disconnect. Close this browser before starting another run.", true); }
  });
  void chrome.storage.local.get({ automationStatus: "Runner not connected." })
    .then((values) => report(values.automationStatus)).catch(() => {});
})();
