"use strict";
(() => {
  const input = document.getElementById("pairing-key");
  const connect = document.getElementById("connect");
  const disconnect = document.getElementById("disconnect");
  const status = document.getElementById("status");
  function show(message, error = false) { status.textContent = message; status.classList.toggle("error", error); }
  connect.addEventListener("click", async () => {
    const key = input.value.trim();
    if (!/^[a-f0-9]{64}$/i.test(key)) { show("Paste the complete pairing key copied from Kula APP.", true); return; }
    connect.disabled = true;
    try {
      const result = await chrome.runtime.sendMessage({ type: "KULA_CHATGPT_CONNECT", key });
      if (!result?.ok) throw new Error(result?.error || "Could not connect to Kula APP.");
      input.value = "";
      show("Connection saved. Keep Kula APP open and start the run there.");
    } catch (error) { show(error.message || "Could not save the connection.", true); }
    finally { connect.disabled = false; }
  });
  disconnect.addEventListener("click", async () => {
    disconnect.disabled = true;
    try { await chrome.runtime.sendMessage({ type: "KULA_CHATGPT_DISCONNECT" }); show("Disconnected. Pending browser steps will stop."); }
    catch { show("Could not disconnect. Stop the run in Kula APP.", true); }
    finally { disconnect.disabled = false; }
  });
  void chrome.storage.local.get({ automationEnabled: false }).then((settings) => show(settings.automationEnabled
    ? "Connection configured. Kula APP controls application runs."
    : "Not connected. Copy the automation key from Kula APP to begin."));
})();
