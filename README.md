# Jobright Apply Filter

A Chrome extension that hides Jobright job cards whose application button says **APPLY NOW** and removes duplicate job cards from view. **EASY APPLY** and **APPLY WITH AUTOFILL** jobs remain eligible to appear, with one card per matching job.

Both filters are on by default. Use the extension's toolbar popup to switch **Hide APPLY NOW jobs** and **Hide duplicate jobs** on or off independently. Your preferences are saved across browser restarts and shared by your open Jobright tabs. Newly loaded job cards are filtered as you browse.

Use **Download job links** in the popup to automatically scroll your current filtered Jobright list and save eligible job links to a text file.

In the companion Windows app, use **Export job links** to open the downloaded TXT file and load its links into the job list.

Kula APP also includes **Start**/**Stop** application automation with the Jobright runner and a new **Kula ChatGPT Connector** extension. See the [automation setup and supported forms](docs/automation.md). Start submits completed applications automatically using the matched resume and saved profile.

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

## Load a TXT file in Kula APP

Kula APP is a Windows x64 desktop app included in this repository. Its **Export job links** button opens a TXT file picker and loads the selected file into the Jobs table. Each successful load **replaces the previous list and clears its resume assignments**.

1. Download and open **KulaAPP.exe** using the [Windows app instructions](kula-app/README.md). The Python-based executable includes everything needed to run; no Python, .NET, or SDK installation is required.
2. Reload the updated extension at `chrome://extensions` and refresh Jobright.
3. Choose your Jobright filters, then use **Download job links** in the extension to save `jobright-job-links.txt`.
4. In Kula APP's **Jobs** tab, click **Export job links** and select that TXT file.
5. Check the loaded links, then import the matching resume folder.

TXT files contain one HTTPS Jobright job URL per line. Kula APP preserves line order and duplicate entries, ignores blank lines, and validates the whole file before replacing the list. Canceling, unreadable files, empty files, and invalid links leave the previous list intact. UTF-8 (including BOM) and UTF-16 with BOM are supported, up to 5 MB and 10000 links. Jobs and resume assignments are kept in memory and start empty when the app restarts.

TXT loading, resume importing, and profile saving work with local files. For optional Start automation, the app opens an authenticated connection on `127.0.0.1:43127`, and the extensions use it to exchange browser commands. The Jobright runner requests HTTPS application-site access when you connect it; filter/download features remain available without starting automation.

The **Build Kula APP for Windows** GitHub Actions workflow builds a package containing the Windows executable, both matching extensions, and an automation guide. After a successful run, the downloadable artifact is named **KulaAPP-windows-x64**.

In Kula APP's **Jobs** tab, click **Import Resumes** to select a folder with one PDF per job link. Files are paired in natural filename order (`name.pdf`, `name (1).pdf`, `name (2).pdf`, …), and the table displays each pairing for review. A count mismatch leaves the previous import intact; new job batches clear the old resume assignments.

The **Package** tab saves your Full Name, LinkedIn Info, US Citizen answer, Not a protected veteran answer, Gender, and Race locally on your computer. See the [app instructions](kula-app/README.md#import-resumes) for importing resumes and saving your profile.

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

The filtering and download features hide cards locally and save job links to a text file. Application actions run when Kula APP starts an authenticated automation run; completed forms are submitted automatically. Browser storage holds filter preferences and the automation connection settings. Website totals can still include the cards hidden by the extension.

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

Run Kula APP's TXT loading, resume import, and profile tests:

```bash
python -m unittest discover -s kula-app/tests -p "test_*.py" -v
```

Additional automation tests exercise the authenticated command channel, sequential runs, submission history, and browser adapters. Graphical Python tests require a desktop session (or a virtual display on Linux). Kula APP uses Python's standard library at runtime, with PyInstaller used only for packaging. GitHub Actions builds and smoke-tests the Windows executable; Linux builds are useful for local validation but do not produce a Windows executable. Tests use local fixtures and do not submit real job applications.
