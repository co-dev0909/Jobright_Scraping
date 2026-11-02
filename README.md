# Jobright Apply Filter

A Chrome extension that hides Jobright job cards whose application button says **APPLY NOW** and removes duplicate job cards from view. **EASY APPLY** and **APPLY WITH AUTOFILL** jobs remain eligible to appear, with one card per matching job.

Both filters are on by default. Use the extension's toolbar popup to switch **Hide APPLY NOW jobs** and **Hide duplicate jobs** on or off independently. Your preferences are saved across browser restarts and shared by your open Jobright tabs. Newly loaded job cards are filtered as you browse.

**Already installed?** At `chrome://extensions`, click the reload button on **Jobright Apply Filter**, then refresh your Jobright tabs. Duplicate filtering starts automatically.

## Install in Chrome

1. Open `chrome://extensions` in Chrome.
2. Turn on **Developer mode** in the upper-right corner.
3. Click **Load unpacked**.
4. Select `D:\Project\Jobright Scraping\extension` — the folder containing `manifest.json`.
5. Open or refresh your Jobright jobs page.
6. Optionally pin **Jobright Apply Filter** from Chrome's Extensions menu (the puzzle-piece button). Click its icon to adjust the filters.

If you are using `jobright-apply-filter.zip`, extract it first. At step 4, select the extracted folder containing `manifest.json`. Keep that folder in place while the extension is installed.

These steps follow [Chrome's instructions for loading an unpacked extension](https://developer.chrome.com/docs/extensions/get-started/tutorial/hello-world#load-unpacked).

## What the filter does

- Hides matching job cards locally in the page. It does not use Jobright's account-level “hide job” action.
- Matches the **APPLY NOW** button label. It does not click application buttons or visit their destinations to determine the provider.
- Keeps the first eligible card when jobs share a Jobright job URL/ID, or the same title, company, and location after text normalization. All three text fields are required for that second match.
- Keeps jobs at different companies or locations separate unless they share the same Jobright job URL/ID. Cards without enough identifying information remain visible.
- Checks application type before duplicates: a hidden **APPLY NOW** card does not suppress a matching **EASY APPLY** or **APPLY WITH AUTOFILL** card.
- Watches for jobs added or changed during browsing, including infinite scrolling.
- Rechecks the current page when cards change or are removed, so the next matching card can become visible.
- Restores cards when you turn off the relevant filter; the other filter can still hide them.

Duplicate detection only compares cards currently in the page. It does not save a history of jobs or suppress matching jobs across tabs or after navigating to a different page.

The extension does not apply for jobs, modify account data, or send data to a backend. It uses browser storage for the two on/off preferences and runs its page script only on Jobright. Website totals can still include the cards hidden by the extension.

## If a card is still visible

After installing or reloading the extension, refresh the Jobright page. Check that the relevant filter is enabled in the popup. Duplicate cards with different job IDs must have matching titles, companies, and locations to be hidden.

Local browser tests use representative job-card fixtures. The live signed-in Jobright page has not been verified. Jobright can change its page structure, which may require an extension update. The filter leaves a card visible when it cannot identify the card boundary safely. Individual `/jobs/info/` pages are excluded.

To install an updated copy, click the extension's reload button at `chrome://extensions`, then refresh Jobright again. To remove the extension, click **Remove** on its extension card.

## Development

The extension uses Manifest V3 and has no build step or package dependencies. Load the `extension` folder directly.

From the source project (tests are not included in the installation ZIP), run the browser tests with Node.js and Microsoft Edge installed:

```powershell
node --test tests/browser.test.mjs
```

The tests use representative local pages rather than your Jobright account.
