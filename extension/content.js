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
  let settingsRevision = 0;
  let collectingLinks = false;

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
    if (!isJobListRoute()) return [];

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
    return [...visitedCards];
  }

  function jobUrlFor(card) {
    const link = jobLinksWithin(card)[0];
    if (link) return `https://jobright.ai/jobs/info/${encodeURIComponent(linkJobId(link))}`;
    // Only synthesize links for unambiguous Jobright-style IDs. Other data IDs
    // may be UI identifiers rather than real job destinations.
    const ids = new Set([card, ...card.querySelectorAll("[data-job-id]")]
      .map((element) => normalizeJobId(element.getAttribute("data-job-id"))).filter(Boolean));
    if (ids.size === 1 && /^[a-f0-9]{24}$/.test([...ids][0])) {
      return `https://jobright.ai/jobs/info/${[...ids][0]}`;
    }
    return null;
  }

  function scrollContainer(card) {
    for (let node = card?.parentElement; node && node !== document.body; node = node.parentElement) {
      if (node.scrollHeight > node.clientHeight && /^(auto|scroll|overlay)$/.test(getComputedStyle(node).overflowY)) {
        return node;
      }
    }
    return document.scrollingElement;
  }

  async function collectJobLinks() {
    if (!isJobListRoute()) return { error: "Open a Jobright job list before downloading links." };
    const pageUrl = location.href;
    const revision = settingsRevision;
    const links = new Set();
    const seenKeys = new Set();
    const skippedCards = new Set();
    const originalPositions = new Map();
    const deadline = Date.now() + 120_000;
    let stableRounds = 0;
    let previousLoadedState;
    let stoppedReason = "limit";

    try {
      // Begin at the top even when the user opened the popup midway through a
      // virtualized list. Earlier cards may no longer exist in the DOM.
      const firstScroller = scrollContainer(scan()[0]);
      if (firstScroller) {
        originalPositions.set(firstScroller, firstScroller.scrollTop);
        if (firstScroller.scrollTop !== 0) {
          firstScroller.scrollTop = 0;
          await new Promise((resolve) => setTimeout(resolve, 700));
        }
      }
      for (let round = 0; round < 200 && Date.now() < deadline; round++) {
        if (location.href !== pageUrl || settingsRevision !== revision) {
          return { error: "The page or filters changed. Download again from the results you want." };
        }
        // Refresh filtering synchronously so a newly inserted or reused card
        // cannot slip into the file before the observer's next scheduled scan.
        const cards = scan();
        const previousCount = links.size;
        for (const card of cards) {
          if (!isVisible(card) || hiddenCards.has(card)) continue;
          const url = jobUrlFor(card);
          if (!url) {
            skippedCards.add(card);
            continue;
          }
          const keys = duplicateKeys(card);
          if (settings.hideDuplicates && [...keys].some((key) => seenKeys.has(key))) continue;
          links.add(url);
          for (const key of keys) seenKeys.add(key);
        }

        const scroller = scrollContainer(cards[0]);
        if (!scroller) break;
        if (!originalPositions.has(scroller)) originalPositions.set(scroller, scroller.scrollTop);
        const beforeTop = scroller.scrollTop;
        const beforeHeight = scroller.scrollHeight;
        // Progress includes jobs excluded by the filters. Otherwise six batches
        // of APPLY NOW jobs could stop collection before a later eligible job.
        const signature = JSON.stringify(cards.map((card) => [
          [...duplicateKeys(card)], normalize(card.textContent),
        ]));
        const loadedMore = !previousLoadedState ||
          previousLoadedState.scroller !== scroller || previousLoadedState.height !== beforeHeight ||
          previousLoadedState.signature !== signature;
        previousLoadedState = { scroller, height: beforeHeight, signature };
        const atBottom = beforeTop + scroller.clientHeight >= beforeHeight - 2;
        if (atBottom && !loadedMore && links.size === previousCount) stableRounds++;
        else stableRounds = 0;
        if (stableRounds >= 6) {
          // A quiet bottom is only an observed stopping point. Slow loading or
          // site limits can still leave more jobs on the server.
          stoppedReason = "stalled";
          break;
        }
        // Never jump farther than a viewport: virtualized short lists could
        // otherwise remove intermediate cards before we have collected them.
        scroller.scrollTop = beforeTop + Math.max(1, Math.floor(scroller.clientHeight * 0.8));
        await new Promise((resolve) => setTimeout(resolve, 700));
      }
      if (location.href !== pageUrl || settingsRevision !== revision) {
        return { error: "The page or filters changed. Download again from the results you want." };
      }
      return { links: [...links], skippedCount: skippedCards.size, stoppedReason };
    } finally {
      if (location.href === pageUrl) {
        for (const [scroller, top] of originalPositions) {
          if (scroller.isConnected) scroller.scrollTop = top;
        }
        scan();
      }
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
      if (changed) {
        settingsRevision++;
        scan();
      }
    });

    chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
      if (message?.type !== "GET_JOB_LINKS") return false;
      if (collectingLinks) {
        sendResponse({ error: "Job links are already being collected. Keep the download popup open." });
        return false;
      }
      collectingLinks = true;
      void collectJobLinks()
        .catch(() => ({ error: "Could not collect job links. Refresh Jobright and try again." }))
        .then((result) => {
          collectingLinks = false;
          sendResponse(result);
        });
      return true;
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
