"use strict";

(() => {
  if (globalThis.KulaChatGPTPage) return;
  let busy = false;
  const pause = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
  const visible = (element) => element && element.getClientRects().length > 0 && getComputedStyle(element).visibility === "visible";
  const composer = () => document.querySelector('#prompt-textarea[contenteditable="true"], textarea#prompt-textarea, textarea[data-testid="prompt-textarea"]');
  const composerText = (element) => element instanceof HTMLTextAreaElement ? element.value : element?.textContent || "";
  const sendButton = () => document.querySelector('button[data-testid="send-button"],button[aria-label="Send prompt"],button[aria-label="Send message"]');
  const enabled = (element) => visible(element) && !element.disabled && element.getAttribute("aria-disabled") !== "true";

  async function authorize(command) {
    const reply = await chrome.runtime.sendMessage({ type: "KULA_CHATGPT_AUTHORIZE", command_id: command.id, run_id: command.run_id });
    if (reply?.ok !== true) throw new Error("Kula APP stopped this ChatGPT request.");
  }

  function checkPage() {
    if (location.protocol !== "https:" || location.hostname !== "chatgpt.com") throw new Error("This is no longer the ChatGPT automation page.");
    if (document.querySelector('iframe[src*="recaptcha"],iframe[src*="challenges.cloudflare.com"],[data-sitekey],#challenge-running')) {
      throw new Error("ChatGPT needs a CAPTCHA or browser check. Complete it yourself, then retry this job.");
    }
    const alerts = [...document.querySelectorAll('[role="alert"],[data-sonner-toast]')].map((element) => element.textContent).join(" ");
    if (/usage limit|message limit|too many requests|too many messages|try again later|you[’']ve reached/i.test(alerts)) {
      throw new Error("ChatGPT reported a usage limit. Wait until your account can continue, then retry this job.");
    }
    const login = [...document.querySelectorAll('a[href*="/auth/login"],button[data-testid*="login"],button')]
      .some((element) => visible(element) && /^(log in|sign in|sign up)$/i.test(element.textContent.trim()));
    if (login) throw new Error("Sign in to ChatGPT in this browser before starting Kula APP.");
  }

  async function waitFor(command, predicate, milliseconds, message) {
    const deadline = Date.now() + milliseconds;
    let nextAuthorization = 0;
    while (Date.now() < deadline) {
      checkPage();
      if (Date.now() >= nextAuthorization) { await authorize(command); nextAuthorization = Date.now() + 1500; }
      const result = predicate();
      if (result) return result;
      await pause(200);
    }
    throw new Error(message);
  }

  function pdfInput() {
    return [...document.querySelectorAll('input[type="file"]')].find((input) => {
      const accept = input.getAttribute("accept") || "";
      return !input.disabled && (!accept || /pdf|application\/\*|\*\/\*/i.test(accept));
    });
  }

  function attachmentVisible(filename) {
    return [...document.querySelectorAll('[data-testid*="file"],[data-testid*="attachment"],[class*="attachment" i],[aria-label]')]
      .some((element) => element.tagName !== "INPUT" && visible(element) && !composer()?.contains(element) &&
        ((element.textContent || "").includes(filename) || (element.getAttribute("aria-label") || "").includes(filename)));
  }

  function uploadBusy() {
    return [...document.querySelectorAll('[role="progressbar"],progress,[data-testid*="upload-progress"],button[aria-label*="Cancel upload" i]')].some(visible);
  }

  async function attachResume(command, resume) {
    let input = pdfInput();
    if (!input) {
      const plus = document.querySelector('button[data-testid="composer-plus-btn"],button[aria-label="Attach files"],button[aria-label="Upload files"]');
      if (!enabled(plus)) throw new Error("ChatGPT's resume attachment control was not found. Its page layout may need an extension update.");
      await authorize(command);
      plus.click();
      input = await waitFor(command, pdfInput, 3000, "ChatGPT's PDF attachment input is unavailable. Close its upload menu and retry after checking the page.");
    }
    const raw = atob(resume.base64);
    const bytes = new Uint8Array(raw.length);
    for (let index = 0; index < raw.length; index++) bytes[index] = raw.charCodeAt(index);
    const transfer = new DataTransfer();
    transfer.items.add(new File([bytes], resume.name, { type: "application/pdf" }));
    await authorize(command);
    input.files = transfer.files;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    await waitFor(command, () => attachmentVisible(resume.name) && !uploadBusy(), 35_000,
      "ChatGPT did not confirm the resume upload. Check its attachment control and retry this job.");
  }

  function insertPrompt(element, prompt) {
    element.focus();
    if (element instanceof HTMLTextAreaElement) {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
      setter.call(element, prompt);
    } else {
      const selection = getSelection();
      const range = document.createRange();
      range.selectNodeContents(element);
      selection.removeAllRanges();
      selection.addRange(range);
      if (!document.execCommand("insertText", false, prompt)) {
        element.replaceChildren(document.createTextNode(prompt));
      }
    }
    element.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: prompt }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function responseText(element) {
    const code = element.querySelector("pre code");
    if (code) return code.textContent;
    const output = (element.querySelector(".markdown") || element).cloneNode(true);
    for (const control of output.querySelectorAll("button,svg")) control.remove();
    return output.textContent;
  }

  async function run(command, payload) {
    if (busy) throw new Error("A ChatGPT answer request is already running.");
    KulaChatGPT.validatePayload(payload);
    busy = true;
    try {
      checkPage();
      const editor = await waitFor(command, composer, 20_000, "ChatGPT's composer was not found. Sign in and check the page layout.");
      if (composerText(editor).trim()) throw new Error("The automation chat has an unsent draft. Keep it unchanged and retry with a fresh automation tab.");
      const previousMessages = new Set(document.querySelectorAll('[data-message-author-role="assistant"]'));
      await attachResume(command, payload.resume);
      await authorize(command);
      insertPrompt(editor, KulaChatGPT.buildPrompt(payload));
      const button = await waitFor(command, () => {
        const send = sendButton();
        return enabled(send) && !uploadBusy() ? send : null;
      }, 20_000, "ChatGPT's send control stayed unavailable. Check the resume upload or account status.");
      await authorize(command);
      button.click();

      let lastText = "";
      let stableSince = 0;
      return await waitFor(command, () => {
        const messages = [...document.querySelectorAll('[data-message-author-role="assistant"]')]
          .filter((element) => !previousMessages.has(element));
        const latest = messages.at(-1);
        if (!latest) return null;
        const text = responseText(latest).trim();
        if (!text) return null;
        if (text !== lastText) { lastText = text; stableSince = Date.now(); return null; }
        const generating = [...document.querySelectorAll('button[data-testid="stop-button"],button[aria-label="Stop generating"],button[aria-label="Stop streaming"]')].some(visible);
        if (generating || Date.now() - stableSince < 1200) return null;
        return KulaChatGPT.parseResponse(text, payload);
      }, 120_000, "ChatGPT did not finish a matching answer in time. Check its automation tab before retrying.");
    } finally { busy = false; }
  }

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (sender.id !== chrome.runtime.id) return false;
    if (message?.type === "KULA_CHATGPT_DRAFT_STATUS") {
      const editor = composer();
      sendResponse({ hasDraft: !editor || !!composerText(editor).trim() });
      return false;
    }
    if (message?.type !== "KULA_CHATGPT_ANSWER") return false;
    void run(message.command, message.payload).then((result) => sendResponse({ ok: true, result }))
      .catch((error) => sendResponse({ ok: false, error: error.message || "ChatGPT could not prepare an answer." }));
    return true;
  });
  globalThis.KulaChatGPTPage = { run };
})();
