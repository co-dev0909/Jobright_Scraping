# Jobright Apply Filter

A Chrome extension that hides Jobright job cards whose application button says **APPLY NOW** and removes duplicate job cards from view. **EASY APPLY** and **APPLY WITH AUTOFILL** jobs remain eligible to appear, with one card per matching job.

Both filters are on by default. Use the extension's toolbar popup to switch **Hide APPLY NOW jobs** and **Hide duplicate jobs** on or off independently. Your preferences are saved across browser restarts and shared by your open Jobright tabs. Newly loaded job cards are filtered as you browse.

Use **Download job links** in the popup to automatically scroll your current filtered Jobright list and save eligible job links to a text file.

Use **Send to Kula APP** to replace the job-link list in the companion Windows app with your current filtered results.

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

## Download job links

1. Open your Jobright job list and choose the website filters you want (such as location, role, experience, remote, and posting date).
2. Open the extension popup and leave **Hide APPLY NOW jobs** and **Hide duplicate jobs** enabled to export the eligible jobs without repeats. The download follows your current extension settings if you change them.
3. Click **Download job links**. Keep the popup open and leave the page and filters unchanged while it collects links.
4. Save **jobright-job-links.txt** when the download starts. It contains one Jobright job URL per line, in collection order, without tracking parameters. Each URL appears once.

Collection starts at the top, scrolls the job list automatically, and retains links from cards removed by virtual scrolling. It uses the results your browser loads under the current website filters; it does not query a separate Jobright API or apply for jobs. The original scroll position is restored afterward.

Collection stops after the bottom stays quiet for about four seconds, or after two minutes / 200 scroll steps. The popup reports how many links were collected. Slow loading, site result limits, and cards without an identifiable job link can prevent a complete export; the popup reports skipped cards and when the collection limit is reached. You can try again after more results load. A card needs a Jobright job link or an unambiguous 24-character hexadecimal job ID to be exported. Navigation or extension filter changes during collection cancel the download so results from different lists are not mixed. Avoid changing website filters during collection, including filters that do not change the page URL.

The download uses your open Jobright tab. No new permissions, backend, or account credentials are required. If you just updated the extension, reload it at `chrome://extensions` and refresh your Jobright tab before downloading.

## Send links to Kula APP

Kula APP is a Windows x64 desktop app included in this repository. Each successful send **clears the previous list and replaces it with the newly collected links**. Invalid batches and failed collections leave the previous list in place. Empty results do not clear the app.

1. Build and open **KulaAPP.exe** using the [Windows app instructions](kula-app/README.md). The .NET 10 SDK is needed to build it; the published executable includes its own runtime.
2. Reload the updated extension at `chrome://extensions`, accept the new local connection permission if prompted, and refresh Jobright.
3. In Kula APP, click **Copy pairing key**. In the extension popup, open **Connect to Kula APP**, paste the key, and click **Save connection**. Do this once per browser profile.
4. Choose the filters you want on Jobright. Keep Kula APP open, click **Send to Kula APP**, and keep the popup open while it collects and sends links.
5. Kula APP replaces its list, and the extension confirms the received link count. A second send replaces that list again rather than appending.

The same collection rules and limits described above apply. If a send times out or the acknowledgement is lost, check Kula APP's list before retrying. The list is kept in memory and starts empty when the app restarts.

This version adds access to `http://127.0.0.1/*` for the connection. The extension sends only to the fixed local endpoint `http://127.0.0.1:43127/v1/job-links`. Kula APP listens only on this computer and requires the saved pairing key. The app does not need your Jobright credentials. Keep the pairing key private.

The **Build Kula APP for Windows** GitHub Actions workflow builds a package containing the Windows executable and matching extension. After a successful run, the downloadable artifact is named **KulaAPP-windows-x64**. A second extension can use the same local protocol when its behavior is defined; this first version implements the Jobright-to-app flow.

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

The extension does not apply for jobs or modify account data. It stores the two preferences and the local Kula pairing key in browser storage, and runs its page script only on Jobright. Job links are sent to the local Kula APP only when you click **Send to Kula APP**. Website totals can still include the cards hidden by the extension.

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

On Linux, use Node.js 22 or newer and a Chromium-compatible browser:

```bash
EDGE_PATH=/usr/bin/chromium node --test tests/browser.test.mjs
```

Run the connection-worker tests and the portable .NET receiver integration tests:

```bash
node --test tests/kula-bridge.test.mjs
dotnet run --project kula-app/tests/Kula.Bridge.Tests/Kula.Bridge.Tests.csproj -c Release
```

The browser tests use representative local pages rather than your Jobright account, including dynamically loaded and virtualized job lists and popup download/send flows. Receiver integration tests exercise real local HTTP transfers, validation, replacement, and acknowledgement behavior. The Windows app can be cross-built on Linux; its actual GUI needs verification on Windows.
