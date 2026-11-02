(() => {
  "use strict";

  const HIDDEN_ATTRIBUTE = "data-jobright-apply-filter-hidden";
  const DEFAULT_SETTINGS = { hideApplyNow: true, hideDuplicates: true };
  const APPLICATION_LABELS = new Set([
    "APPLY NOW",
    "EASY APPLY",
    "APPLY WITH AUTOFILL",
  ]);
  const CONTROL_SELECTOR =
    'button, a, [role="button"], input[type="button"], input[type="submit"]';
  const EXCLUDED_REGIONS = [
    "body > header", "nav", "body > footer", "aside",
    '[role="dialog"]', '[role="alertdialog"]',
    '[data-testid="job-detail"]', '[data-testid="job-details"]',
    '[class*="job-detail" i]', '[class*="jobDetail" i]', '[class*="job_detail" i]',
    '[contenteditable="true"]',
  ].join(",");
  const METADATA = {
    title: {
      attributes: ["data-job-title"],
      selectors: ['[data-testid="job-title"]', '[class*="jobTitle" i]', '[class*="job-title" i]', '[class*="job_title" i]', "h2", "h3", "h4"],
    },
    company: {
      attributes: ["data-company-name", "data-company"],
      selectors: ['[data-testid="company-name"]', '[data-testid="job-company"]', '[class*="companyName" i]', '[class*="company-name" i]', '[class*="company_name" i]'],
    },
    location: {
      combine: true,
      attributes: ["data-job-location", "data-location"],
      selectors: ['[data-testid="job-location"]', '[data-testid="location"]', '[class*="jobLocation" i]', '[class*="job-location" i]', '[class*="job_location" i]', '[class*="location" i]'],
    },
  };
  const hiddenCards = new Set();
  let settings = { ...DEFAULT_SETTINGS };
  let scanTimer;
  let previousUrl = location.href;

  function normalize(text) {
    return (text || "").replace(/\s+/g, " ").trim().toUpperCase();
  }

  function labelOf(control) {
    const text = control instanceof HTMLInputElement ? control.value : control.textContent;
    return normalize(text || control.getAttribute("aria-label"));
  }

  function isJobListRoute() {
    return /^\/jobs(?:\/|$)/i.test(location.pathname) &&
      !/^\/jobs\/(?:info|details?)(?:\/|$)/i.test(location.pathname);
  }

  function jobLinksWithin(element) {
    const links = [];
    if (element.matches('a[href]')) links.push(element);
    links.push(...element.querySelectorAll('a[href*="/jobs/info/"]'));
    return links.filter((link) => {
      try {
        const url = new URL(link.getAttribute("href"), location.href);
        return ["jobright.ai", "www.jobright.ai"].includes(url.hostname) &&
          /^\/jobs\/info\/[^/]+\/?$/.test(url.pathname);
      } catch {
        return false;
      }
    });
  }

  function hasJobIdentity(element) {
    return jobLinksWithin(element).length > 0 ||
      Boolean(element.getAttribute("data-job-title") ||
        element.querySelector("h2, h3, h4, [data-testid='job-title'], [data-job-title]"));
  }

  function normalizeJobId(value) {
    const id = (value || "").trim();
    return /^[a-f0-9]{24}$/i.test(id) ? id.toLowerCase() : id;
  }

  function linkJobId(link) {
    const id = new URL(link.href).pathname.split("/")[3];
    try {
      return normalizeJobId(decodeURIComponent(id));
    } catch {
      return normalizeJobId(id);
    }
  }

  function metadataValue(card, field) {
    // Require separately identifiable fields; never guess company/location
    // from the whole card, which also contains timestamps and match scores.
    const combined = new Set();
    for (const attribute of field.attributes) {
      const value = card.getAttribute(attribute) ||
        card.querySelector(`[${attribute}]`)?.getAttribute(attribute);
      if (normalize(value)) {
        if (!field.combine) return normalize(value);
        combined.add(normalize(value));
      }
    }
    if (field.combine) {
      // Geography and Remote/Hybrid labels can live in separate elements.
      // Include all location fields so Remote US and Remote Canada differ.
      const elements = [...card.querySelectorAll(field.selectors.join(","))];
      for (const element of elements) {
        if (elements.some((other) => other !== element && other.contains(element))) continue;
        const value = normalize(element.textContent);
        if (value) combined.add(value);
      }
      return [...combined].sort().join(" | ");
    }
    for (const selector of field.selectors) {
      const values = [...card.querySelectorAll(selector)]
        .map((element) => normalize(element.textContent)).filter(Boolean);
      if (values.length) return [...new Set(values)].join(" | ");
    }
    return "";
  }

  function duplicateKeys(card) {
    const keys = new Set(jobLinksWithin(card).map((link) => `id:${linkJobId(link)}`));
    const dataIds = new Set([card, ...card.querySelectorAll("[data-job-id]")]
      .map((element) => normalizeJobId(element.getAttribute("data-job-id"))).filter(Boolean));
    if (dataIds.size === 1) keys.add(`id:${[...dataIds][0]}`);

    const fields = [METADATA.title, METADATA.company, METADATA.location]
      .map((field) => metadataValue(card, field));
    if (fields.every(Boolean)) keys.add(`details:${JSON.stringify(fields)}`);
    return keys;
  }

  function isVisible(element) {
    return element.getClientRects().length > 0 &&
      getComputedStyle(element).visibility === "visible";
  }

  function isNamedCard(element) {
    return element.matches('[data-job-id], [data-testid="job-card"], [data-testid^="job-card-"]') ||
      [...element.classList].some((name) =>
        /(?:^|[_-])job[_-]?card(?:[_-]|$)/i.test(name) &&
        !/job[_-]?card[_-](?:list|grid)(?:[_-]|$)/i.test(name));
  }

  function isSemanticCard(element) {
    return element.matches('article, li, [role="listitem"]');
  }

  function findCard(control, actions) {
    let namedCandidate = null;
    let semanticCandidate = null;
    let linkedCandidate = null;

    for (let node = control.parentElement; node && node !== document.body; node = node.parentElement) {
      // Never hide a list, page region, or detail pane as if it were one job.
      if (node.matches('main, [role="main"], [role="list"], ul, ol') || node.matches(EXCLUDED_REGIONS)) break;
      const containedActions = actions.filter((action) => node.contains(action));
      if (containedActions.length !== 1) break;
      if (!hasJobIdentity(node)) continue;

      const jobUrls = new Set(jobLinksWithin(node).map(linkJobId));
      if (jobUrls.size > 1) break;
      // Inner sections may also be named job-card-content. Prefer the outer
      // named card so its match-score column disappears with the job text.
      if (isNamedCard(node)) namedCandidate = node;
      if (isSemanticCard(node) &&
          (jobUrls.size === 1 || namedCandidate || node.querySelector('[data-testid="job-title"]'))) {
        semanticCandidate = node;
      }
      // For unknown markup, require peer jobs before accepting a plain div.
      // A title/CTA section alone may leave the card's score column behind.
      if (jobUrls.size === 1 && [...node.parentElement.children].some((sibling) =>
        sibling !== node && hasJobIdentity(sibling) && actions.some((action) => sibling.contains(action)))) {
        linkedCandidate = node;
      }
    }

    // These candidates all lie on the same ancestor chain. The outermost
    // recognized boundary includes any sibling score panel or card footer.
    return [semanticCandidate, namedCandidate, linkedCandidate].filter(Boolean).reduce(
      (outer, candidate) => !outer || candidate.contains(outer) ? candidate : outer,
      null,
    );
  }

  function restoreCards() {
    for (const card of hiddenCards) card.removeAttribute(HIDDEN_ATTRIBUTE);
    hiddenCards.clear();
  }

  function scan() {
    if (scanTimer !== undefined) clearTimeout(scanTimer);
    scanTimer = undefined;
    // Start from the site's own visibility. A duplicate must become eligible
    // again if its earlier copy was removed, changed, or hidden by the site.
    restoreCards();
    if ((!settings.hideApplyNow && !settings.hideDuplicates) || !isJobListRoute()) return;

    const candidates = [...document.querySelectorAll(CONTROL_SELECTOR)].filter((control) =>
      APPLICATION_LABELS.has(labelOf(control)) && !control.closest(EXCLUDED_REGIONS) && isVisible(control));
    // Some controls contain another element with role=button. Count that CTA once.
    const actions = candidates.filter((control) =>
      !candidates.some((other) => other !== control && control.contains(other)));
    const nextHidden = new Set();
    const visitedCards = new Set();
    const seenKeys = new Set();
    for (const control of actions) {
      const card = findCard(control, actions);
      if (!card || visitedCards.has(card) || !isVisible(card)) continue;
      visitedCards.add(card);
      // A filtered application type must not suppress a usable copy later on.
      if (settings.hideApplyNow && labelOf(control) === "APPLY NOW") {
        nextHidden.add(card);
        continue;
      }
      if (settings.hideDuplicates) {
        const keys = duplicateKeys(card);
        if ([...keys].some((key) => seenKeys.has(key))) nextHidden.add(card);
        else for (const key of keys) seenKeys.add(key);
      }
    }
    for (const card of nextHidden) {
      if (card.getAttribute(HIDDEN_ATTRIBUTE) !== "true") card.setAttribute(HIDDEN_ATTRIBUTE, "true");
      hiddenCards.add(card);
    }
  }

  function scheduleScan() {
    // Coalesce React/infinite-scroll updates; do not observe our own marker.
    if (scanTimer === undefined) scanTimer = setTimeout(scan, 80);
  }

  async function start() {
    try {
      const stored = await chrome.storage.local.get(DEFAULT_SETTINGS);
      for (const key of Object.keys(DEFAULT_SETTINGS)) settings[key] = stored[key] !== false;
    } catch {
      // The default filter remains useful if extension storage is unavailable.
    }

    chrome.storage.onChanged.addListener((changes, area) => {
      if (area !== "local") return;
      let changed = false;
      for (const key of Object.keys(DEFAULT_SETTINGS)) {
        if (changes[key]) {
          settings[key] = changes[key].newValue !== false;
          changed = true;
        }
      }
      if (changed) scan();
    });

    new MutationObserver(scheduleScan).observe(document.documentElement, {
      childList: true,
      subtree: true,
      characterData: true,
      attributes: true,
      attributeFilter: ["class", "style", "hidden", "role", "href", "aria-label", "value", "data-job-id", "data-testid",
        ...Object.values(METADATA).flatMap((field) => field.attributes)],
    });
    addEventListener("popstate", scheduleScan);
    addEventListener("hashchange", scheduleScan);
    addEventListener("resize", scheduleScan);
    // pushState in the site's JS world is not visible to a content-script override.
    setInterval(() => {
      if (location.href !== previousUrl) {
        previousUrl = location.href;
        scheduleScan();
      }
    }, 750);
    scan();
  }

  start();
})();
