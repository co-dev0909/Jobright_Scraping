"use strict";

(() => {
  if (globalThis.KulaApplicationRunner) return;
  const visible = (element) => element.getClientRects().length > 0 && getComputedStyle(element).visibility === "visible";
  const normalize = (value) => (value || "").replace(/\s+/g, " ").trim();
  const upper = (value) => normalize(value).toUpperCase();
  const nextLabels = new Set(["NEXT", "NEXT STEP", "CONTINUE", "SAVE AND CONTINUE", "REVIEW", "REVIEW APPLICATION", "CONTINUE APPLICATION"]);
  const finalLabels = new Set(["SUBMIT APPLICATION", "SEND APPLICATION", "SUBMIT", "APPLY", "APPLY NOW", "FINISH APPLICATION", "COMPLETE APPLICATION"]);
  const entryLabels = new Set(["APPLY", "APPLY NOW", "APPLY FOR THIS JOB", "START APPLICATION"]);
  const ids = new WeakMap();
  const clickedEntries = new WeakSet();
  let nextId = 1;
  let snapshot;

  function safeUrl(value) {
    const url = new URL(value, location.href);
    const host = url.hostname.replace(/\.$/, "");
    return url.protocol === "https:" && !url.username && !url.password && !url.port && host &&
      host !== "localhost" && !/\.(?:localhost|local|internal|lan)$/.test(host) &&
      !/^\d+(?:\.\d+){3}$/.test(host) && !host.includes(":");
  }

  function safePage() {
    if (!safeUrl(location.href)) throw new Error("Unsupported application destination.");
  }

  const required = (element) => element.required || element.getAttribute("aria-required") === "true";

  function textOf(element) {
    return normalize(element instanceof HTMLInputElement ? element.value : element.innerText || element.textContent);
  }

  function entryLabel(element) {
    // Jobright's external-application arrow is decoration, not part of the
    // launch action's label. Keep exact matching for the remaining text.
    const copy = element.cloneNode(true);
    for (const icon of copy.querySelectorAll('svg,[aria-hidden="true"]')) icon.remove();
    const text = normalize(copy.textContent || element.getAttribute("aria-label"));
    return upper(text.replace(/\s*↗[\uFE0E\uFE0F]?\s*$/u, ""));
  }

  function labelOf(element) {
    const labels = element.labels ? [...element.labels].map((label) => label.textContent).join(" ") : "";
    const labelledBy = (element.getAttribute("aria-labelledby") || "").split(/\s+/).filter(Boolean)
      .map((id) => document.getElementById(id)?.textContent || "").join(" ");
    return normalize(labels || element.getAttribute("aria-label") || labelledBy || element.getAttribute("placeholder") || element.name || element.id).slice(0, 1000);
  }

  function fieldId(element) {
    if (!ids.has(element)) ids.set(element, `field-${nextId++}`);
    return ids.get(element);
  }

  function controlsWithin(root) {
    return [...root.querySelectorAll("input, textarea, select")].filter((element) =>
      !element.disabled && !["hidden", "submit", "button", "reset", "image"].includes(element.type) &&
      (element.type === "file" || visible(element)));
  }

  function buttonsWithin(root) {
    return [...root.querySelectorAll('button, input[type="submit"], input[type="button"], a[role="button"]')]
      .filter((element) => visible(element) && !element.disabled && element.getAttribute("aria-disabled") !== "true");
  }

  function confirmation() {
    const pattern = /^(?:(?:your )?application (?:was |has been )?(?:successfully )?(?:submitted|received|complete)|thank you for applying|thanks for applying|we(?:'|’)ve received your application|you have already applied)\b/i;
    return [...document.querySelectorAll('h1,h2,h3,h4,[role="alert"],[role="status"],[data-testid*="success" i],[class*="success" i]')]
      .some((element) => visible(element) && normalize(element.textContent).length <= 1200 && pattern.test(normalize(element.textContent)));
  }

  function blocker(root = document) {
    if ([...document.querySelectorAll('iframe[src*="captcha" i], [data-sitekey], .g-recaptcha, .h-captcha')].some(visible) ||
        /(?:verify (?:that )?you(?:'|’)re human|complete (?:the |this )?captcha)/i.test((document.body?.innerText || "").slice(0, 10000))) {
      return "This application needs a CAPTCHA or human verification.";
    }
    if (controlsWithin(root).some((element) => element.type === "password")) return "This application requires a login. Sign in manually.";
    for (const element of controlsWithin(root)) {
      if (required(element) && /\b(?:agree|consent|certify|acknowledge|signature|sign here|accept.*terms)\b/i.test(labelOf(element))) {
        return "A required consent, certification, or signature needs your attention.";
      }
    }
    return null;
  }

  function chooseForm() {
    const candidates = [...document.querySelectorAll('form,[role="dialog"]')].filter(visible).map((root) => {
      const controls = controlsWithin(root);
      const buttons = buttonsWithin(root);
      const file = controls.some((element) => element.type === "file");
      const action = buttons.some((button) => nextLabels.has(upper(textOf(button))) || finalLabels.has(upper(textOf(button))));
      const named = /application|applicant|candidate|apply/i.test(`${root.id} ${root.className} ${root.getAttribute("aria-label") || ""}`);
      const requiredCount = controls.filter(required).length;
      return { root, score: controls.length + (file ? 20 : 0) + (named ? 12 : 0) + requiredCount * 2,
        eligible: controls.length > 0 && action && (file || named || requiredCount >= 2 || controls.length >= 3) };
    }).filter((entry) => entry.eligible).sort((a, b) => b.score - a.score);
    if (!candidates.length) return null;
    if (candidates[1] && candidates[0].score === candidates[1].score &&
        !candidates[0].root.contains(candidates[1].root) && !candidates[1].root.contains(candidates[0].root)) return null;
    return candidates[0].root;
  }

  async function inspect() {
    safePage();
    if (confirmation()) return { complete: true, fields: [], next_action: "none", submit_ready: false, url: location.href };
    const globalBlock = blocker();
    if (globalBlock) return { blocked: globalBlock, fields: [] };
    const root = chooseForm();
    if (!root) return { blocked: "No supported application form was found in the main page.", fields: [] };
    const blocked = blocker(root);
    if (blocked) return { blocked, fields: [] };
    const controls = controlsWithin(root);
    if (controls.length > 100) return { blocked: "This application has too many fields for this runner.", fields: [] };
    const entries = [];
    const radioNames = new Set();
    for (const element of controls) {
      if (element.type === "radio") {
        const groupName = element.name || fieldId(element);
        if (radioNames.has(groupName)) continue;
        radioNames.add(groupName);
        const group = controls.filter((other) => other.type === "radio" && (other.name || fieldId(other)) === groupName);
        const legend = element.closest("fieldset")?.querySelector("legend")?.textContent;
        entries.push({ element, group, description: {
          id: fieldId(element), label: normalize(legend || element.getAttribute("aria-label") || element.name || labelOf(element)).slice(0, 1000), type: "radio",
          required: group.some(required), options: group.map((other) => ({ value: other.value, label: labelOf(other) })),
          current_value: group.find((other) => other.checked)?.value || "", readonly: false,
        } });
        continue;
      }
      let type = element instanceof HTMLSelectElement ? (element.multiple ? "select-multiple" : "select") :
        element instanceof HTMLTextAreaElement ? "textarea" : element.type || "text";
      const options = element instanceof HTMLSelectElement ? [...element.options]
        .filter((option) => !option.disabled && option.value !== "").map((option) => ({ value: option.value, label: normalize(option.textContent).slice(0, 500) })) : [];
      if (options.length > 200) return { blocked: "An application option list is too large for this runner.", fields: [] };
      entries.push({ element, description: {
        id: fieldId(element), label: labelOf(element), type, required: Boolean(required(element)), options,
        readonly: Boolean(element.readOnly), existing_file: type === "file" && element.files.length > 0,
        current_value: type === "file" ? "" : type === "checkbox" ? element.checked : type === "select-multiple" ? [...element.selectedOptions].map((option) => option.value) : String(element.value || "").slice(0, 2000),
      } });
    }
    const buttons = buttonsWithin(root);
    const next = buttons.find((button) => nextLabels.has(upper(textOf(button))));
    const submit = buttons.find((button) => finalLabels.has(upper(textOf(button))));
    const signature = JSON.stringify([location.href, entries.map(({ description }) => {
      const { current_value, existing_file, ...identity } = description;
      return identity;
    }), next && textOf(next), submit && textOf(submit)]);
    if (!snapshot || snapshot.signature !== signature || snapshot.root !== root) {
      snapshot = { signature, root, token: crypto.randomUUID(), entries, next, submit };
    } else Object.assign(snapshot, { entries, next, submit });
    return {
      fields: entries.map((entry) => entry.description), url: location.href,
      context: normalize((document.querySelector("main") || root).innerText).slice(0, 18000), form_token: snapshot.token,
      next_action: next ? "next" : submit ? "submit" : "none", submit_ready: Boolean(!next && submit),
    };
  }

  async function authorize(command) {
    const response = await chrome.runtime.sendMessage({ type: "KULA_AUTHORIZE", command_id: command.id, run_id: command.run_id });
    if (response?.ok !== true) throw new Error("The application run stopped or lost authorization.");
  }

  function nativeValue(element, value) {
    const prototype = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype :
      element instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, "value").set.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function errors() {
    if (!snapshot) return [];
    return controlsWithin(snapshot.root).filter((element) => !element.checkValidity() ||
      (required(element) && (element.type === "checkbox" ? !element.checked : element.type === "radio" ?
        !snapshot.entries.find((entry) => entry.group?.includes(element))?.group.some((radio) => radio.checked) :
        element.type === "file" ? element.files.length === 0 : !element.value)))
      .map((element) => labelOf(element) || "Required application field");
  }

  async function fill(payload, command) {
    const current = await inspect();
    if (current.blocked) return current;
    if (!snapshot || current.form_token !== payload.form_token) return { blocked: "The application form changed before it could be filled." };
    if (!Array.isArray(payload.answers) || payload.answers.length > 100) return { blocked: "Invalid application answers." };
    const seen = new Set();
    for (const answer of payload.answers) {
      const entry = snapshot.entries.find((item) => item.description.id === answer?.field_id);
      if (!entry || seen.has(answer.field_id) || entry.description.readonly || entry.description.type === "file") return { blocked: "An answer does not match the current form." };
      seen.add(answer.field_id);
      const { type, options } = entry.description;
      const value = answer.value;
      if (typeof value === "string" && entry.element.maxLength >= 0 && value.length > entry.element.maxLength) {
        return { blocked: "An answer exceeds the application's field length limit." };
      }
      if (type === "checkbox" ? typeof value !== "boolean" : type === "select-multiple" ?
        !Array.isArray(value) || value.some((item) => typeof item !== "string" || !options.some((option) => option.value === item)) :
        typeof value !== "string" || value.length > 20000 || (["select", "radio"].includes(type) && !options.some((option) => option.value === value))) {
        return { blocked: "An answer does not match the form's field type or choices." };
      }
    }
    for (const answer of payload.answers) {
      const entry = snapshot.entries.find((item) => item.description.id === answer.field_id);
      if (!entry.element.isConnected) return { blocked: "The application changed while answers were being filled." };
      await authorize(command);
      if (entry.description.type === "radio") {
        const chosen = entry.group.find((element) => element.value === answer.value);
        // Native click follows React's radio/checkbox change tracking. Assigning
        // .checked and dispatching change alone can leave React state stale.
        if (!chosen.checked) chosen.click();
        if (!chosen.checked) return { blocked: "The application did not keep the selected radio answer." };
      } else if (entry.description.type === "checkbox") {
        if (entry.element.checked !== answer.value) entry.element.click();
        if (entry.element.checked !== answer.value) return { blocked: "The application did not keep the selected checkbox answer." };
      } else if (entry.description.type === "select-multiple") {
        for (const option of entry.element.options) option.selected = answer.value.includes(option.value);
        entry.element.dispatchEvent(new Event("change", { bubbles: true }));
      } else nativeValue(entry.element, answer.value);
    }
    let resumeUploaded = false;
    let resumeSha256;
    const files = snapshot.entries.filter((entry) => entry.description.type === "file");
    if (files.length) {
      const resumeFields = files.filter((entry) => /\b(?:resume|cv|curriculum vitae)\b/i.test(entry.description.label));
      const target = resumeFields.length === 1 ? resumeFields[0] : files.length === 1 && !/cover letter/i.test(files[0].description.label) ? files[0] : null;
      if (!target) return { blocked: "The resume upload field could not be identified safely." };
      const file = payload.resume;
      if (!file || file.mime !== "application/pdf" || typeof file.base64 !== "string" || file.base64.length > 12_000_000 ||
          typeof file.name !== "string" || typeof file.sha256 !== "string" || !/^[a-f0-9]{64}$/i.test(file.sha256)) return { blocked: "The matching PDF resume is unavailable." };
      let bytes;
      try { bytes = Uint8Array.from(atob(file.base64), (character) => character.charCodeAt(0)); }
      catch { return { blocked: "The matching PDF resume could not be read." }; }
      if (bytes.length > 8 * 1024 * 1024 || new TextDecoder().decode(bytes.subarray(0, 5)) !== "%PDF-") return { blocked: "The matching resume is not a supported PDF." };
      const digest = async (data) => [...new Uint8Array(await crypto.subtle.digest("SHA-256", data))]
        .map((byte) => byte.toString(16).padStart(2, "0")).join("");
      resumeSha256 = await digest(bytes);
      if (resumeSha256 !== file.sha256.toLowerCase()) return { blocked: "The matching resume's checksum did not match." };
      const transfer = new DataTransfer();
      const filename = file.name.split(/[\\/]/).pop();
      if (!filename || !/\.pdf$/i.test(filename) || filename.length > 255) return { blocked: "The matching resume filename is invalid." };
      transfer.items.add(new File([bytes], filename, { type: "application/pdf" }));
      await authorize(command);
      target.element.files = transfer.files;
      target.element.dispatchEvent(new Event("input", { bubbles: true }));
      target.element.dispatchEvent(new Event("change", { bubbles: true }));
      const attached = target.element.files;
      if (attached.length !== 1 || attached[0].name !== filename || attached[0].size !== bytes.length ||
          await digest(await attached[0].arrayBuffer()) !== resumeSha256) {
        return { blocked: "The application did not retain the matching resume file." };
      }
      resumeUploaded = true;
    }
    return { ok: true, validation_errors: errors(), resume_uploaded: resumeUploaded,
      ...(resumeUploaded ? { resume_sha256: resumeSha256 } : {}) };
  }

  async function advance(payload, command) {
    const current = await inspect();
    if (current.complete) return { complete: true, submitted: true };
    if (current.blocked) return current;
    if (!snapshot || payload.form_token !== current.form_token) return { blocked: "The application form changed before navigation." };
    const button = payload.mode === "next" ? snapshot.next : payload.mode === "submit" ? snapshot.submit : null;
    if (!button || (payload.mode === "submit" && snapshot.next)) return { blocked: "No unambiguous application action was found." };
    const validation = errors();
    if (validation.length) return { blocked: "Required application fields remain incomplete.", validation_errors: validation };
    await authorize(command);
    button.click();
    const deadline = Date.now() + (payload.mode === "submit" ? 15000 : 5000);
    while (Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 200));
      if (confirmation()) return { complete: true, submitted: true };
      if (payload.mode === "next") {
        const nextForm = await inspect();
        if (nextForm.form_token && nextForm.form_token !== payload.form_token) return { complete: false };
        if (nextForm.blocked) return nextForm;
      }
    }
    return payload.mode === "submit" ? { submitted: false, blocked: "Submission was clicked but no success confirmation was observed. Check this application manually; do not retry automatically." } :
      { blocked: "The application did not advance to another supported page." };
  }

  async function openEntry(command) {
    safePage();
    if (confirmation()) return { already_applied: true };
    const blocked = blocker();
    if (blocked) return { blocked };
    if (chooseForm()) return { ready: true };
    const jobright = ["jobright.ai", "www.jobright.ai"].includes(location.hostname);
    const jobrightDetail = jobright && /^\/jobs\/info\/[A-Za-z0-9_-]{1,200}\/?$/.test(location.pathname);
    const allowed = jobright ? new Set(["EASY APPLY", "APPLY WITH AUTOFILL"]) : entryLabels;
    const candidates = [...document.querySelectorAll('button,a,[role="button"]')].filter((element) => visible(element) &&
      !element.disabled && element.getAttribute("aria-disabled") !== "true" && allowed.has(entryLabel(element)));
    if (candidates.length !== 1 || clickedEntries.has(candidates[0])) return { blocked: "Could not identify a single supported application entry button." };
    const button = candidates[0];
    const navigation = button instanceof HTMLAnchorElement && button.hasAttribute("href");
    const controlsId = button.getAttribute("aria-controls");
    const dialogOpener = button.getAttribute("aria-haspopup") === "dialog" ||
      (controlsId && document.getElementById(controlsId)?.matches('[role="dialog"],dialog')) ||
      /^(?:open-application|open-apply-modal|application-modal-trigger)$/.test(button.getAttribute("data-testid") || "");
    // On Jobright detail pages these specific controls launch the employer's
    // application flow. Plain buttons do not necessarily expose a link or
    // dialog attribute. Elsewhere, retain the one-click-submit guard.
    const jobrightLauncher = jobrightDetail && !button.closest("form") && !button.form &&
      button.getAttribute("type")?.toLowerCase() !== "submit" &&
      (button instanceof HTMLButtonElement || button.getAttribute("role") === "button");
    if (!navigation && !dialogOpener && !jobrightLauncher) return { blocked: "This apply button may submit immediately. Open its editable application manually." };
    if (navigation && !safeUrl(button.href)) return { blocked: "Unsupported application link." };
    await authorize(command);
    clickedEntries.add(button);
    button.click();
    return { clicked: true };
  }

  globalThis.KulaApplicationRunner = {
    async run(action, payload = {}, command = {}) {
      if (action === "inspect_form") return inspect();
      if (action === "fill_form") return fill(payload, command);
      if (action === "advance") return advance(payload, command);
      if (action === "open_entry") return openEntry(command);
      if (action === "confirmation") return { confirmed: confirmation() };
      throw new Error("Unsupported application runner action.");
    },
  };
})();
