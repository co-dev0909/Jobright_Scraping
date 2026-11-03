# Kula APP for Windows

Kula APP loads job links from a local TXT file and displays them in the **Jobs** tab. Import a matching folder of resume PDFs to see each job and resume together. Each successful TXT load **replaces the previous list and clears its resume assignments**. Jobs and resume assignments stay in memory; the **Package** tab saves your profile across app restarts.

The **Start** button processes prepared jobs sequentially using the Jobright runner and the new ChatGPT extension, and automatically submits completed applications. **Stop** prevents further steps. The package includes **AUTOMATION.md** with the connection setup, supported forms, and history behavior; the same guide is available [in the repository](https://github.com/co-dev0909/Jobright_Scraping/blob/main/docs/automation.md).

## Download and run

In the repository's GitHub **Actions** tab, open the latest successful **Build Kula APP for Windows** run and download **KulaAPP-windows-x64** from its artifacts. Extract the ZIP and run **KulaAPP.exe**. Load **extension** in your Jobright browser profile and **chatgpt-extension** in the profile signed in to ChatGPT, using `chrome://extensions` and **Load unpacked**.

The app is written in Python. The single executable bundles Python and Tcl/Tk, so **you do not need to install Python, .NET, or any SDK to run it**. No administrator setup is required.

Artifacts become available only after the workflow finishes successfully. They expire after 30 days; a new run creates a fresh package. If GitHub Actions is disabled or you prefer to build locally, use the steps below.

## About the executable

GitHub Actions builds the executable from this repository, tests TXT loading and the UI, and smoke-tests the packaged executable before uploading it. The package includes **SHA256SUMS.txt** for checking the executable's hash. This is an unsigned app: Windows may show an unknown-publisher or SmartScreen warning. Bundling Python does not provide code signing or a guarantee that Windows will trust the app. Do not disable Windows security protections to run it.

The job list and resume assignments stay in memory. The app stores its saved profile, automation key, and submission history under `%LOCALAPPDATA%\KulaAPP`. Imported resumes are referenced in their original folder, and the selected TXT file is left unchanged. The local automation listener starts when you set up the connection or use Start; ordinary TXT/resume/profile use does not open it. PyInstaller's one-file executable extracts its bundled runtime to a temporary directory while it runs.

## Run or build from source (developers only)

Building is optional. Developers need Python 3.12 with Tkinter; users of the downloaded executable need no runtime installation. Run the source directly:

```powershell
python ./kula-app/kula_app.py
```

Or build a standalone Windows executable:

```powershell
./kula-app/publish-windows.ps1
./kula-app/artifacts/win-x64/KulaAPP.exe
```

The build script creates an isolated Python environment, installs the pinned PyInstaller build tool, and bundles the app. If local PowerShell policy prevents running a script, use these commands directly:

```powershell
python -m venv ./kula-app/.build-venv
./kula-app/.build-venv/Scripts/python.exe -m pip install -r ./kula-app/requirements-build.txt
./kula-app/.build-venv/Scripts/python.exe -m PyInstaller --onefile --windowed --noupx --clean --noconfirm --name KulaAPP --distpath ./kula-app/artifacts/win-x64 --workpath ./kula-app/artifacts/build --specpath ./kula-app/artifacts/spec ./kula-app/kula_app.py
```

Run the application tests and the UI smoke test:

```powershell
python -m unittest discover -s kula-app/tests -p "test_*.py" -v
python ./kula-app/kula_app.py --smoke-test
```

The UI smoke test needs a graphical session. Windows executables must be built on Windows; a Linux build produces a Linux executable.

## Export job links (load a TXT file)

1. In the Jobright extension, click **Download job links** to save `jobright-job-links.txt`.
2. In Kula APP's **Jobs** tab, click **Export job links**.
3. Select the TXT file in the file-open dialog.
4. Kula APP validates the file and replaces the job list with its links in line order.

The button is named **Export job links**, and its action loads links **from** a TXT file into Kula APP. Use one HTTPS Jobright job URL per line, for example:

```text
https://jobright.ai/jobs/info/0123456789abcdef01234567
https://jobright.ai/jobs/info/abcdef0123456789abcdef01
```

Blank lines are ignored. Duplicate links remain in place to preserve the row order for resumes. Jobright URLs are normalized by removing tracking parameters and fragments. TXT filenames are case-insensitive; UTF-8 (including BOM) and UTF-16 with BOM are supported. The maximum file size is 5 MB, with at most 10000 links.

Canceling the dialog or selecting an unreadable, empty, oversized, or invalid file leaves the current jobs and resume assignments intact. A valid load clears old resume assignments, even if the file contains the same links. These import operations use local files.

## Import Resumes

1. Load your TXT file into Kula APP using **Export job links**, then stay on its **Jobs** tab.
2. Put the matching resume PDFs directly inside one folder. The PDF count must equal the job-link count.
3. Click **Import Resumes** and select that folder.
4. Review the **Job link** and **Resume PDF** columns to confirm every pairing.

Files are matched to the displayed job order using natural filename order. For the filenames you described:

| Job row | Resume filename |
| --- | --- |
| 1 | `name.pdf` |
| 2 | `name (1).pdf` |
| 3 | `name (2).pdf` |

Numbered copies sort numerically, so `name (2).pdf` comes before `name (10).pdf`. Other filenames are sorted naturally by name. The app uses this order rather than analyzing resume content to infer a job match; rename files if needed to align them with the job rows.

Only direct `.pdf` files are imported, including uppercase `.PDF` extensions. Other file types, subfolders, and symbolic links are ignored. Kula APP checks readability and the PDF header, but does not assess ATS suitability or fully validate PDF structure. Files remain in the selected folder without being copied or changed.

Canceling the folder picker or selecting an invalid folder leaves the previous assignments intact. A count mismatch reports the actual PDF and job counts. Loading another TXT file clears the old resume assignments, including when it contains the same links, so each list gets its own import.

## Package profile

Open **Package**, enter your information, and click **Save Profile**:

- Full Name
- LinkedIn Info (profile URL or handle)
- US Citizen
- Not a protected veteran
- Gender
- Race

Citizenship and veteran answers start **Not specified**. Gender and Race accept listed choices or your own text, and also start unspecified. You choose all answers.

The profile is saved locally in `%LOCALAPPDATA%\KulaAPP\profile.json` and loaded when you reopen the app. Changes are saved when you click **Save Profile**. The file contains the profile as local JSON; jobs and resume paths are not included in it.

## Start applications

1. Install both extensions and sign in to Jobright and ChatGPT in their respective browser profiles.
2. In **Automation**, click **Copy connection key**, then paste and connect using it in both extension popups. Wait until both browsers report connected.
3. Load the TXT list, import the matching resumes, and save the Package profile. Check the job/resume pairings before starting.
4. Click **Start**. Completed application forms are submitted automatically, and each job's Status is updated.
5. Click **Stop** to cancel the next steps. A submission already in progress may finish; a submitted application cannot be undone by Stop.

Your TXT file contains Jobright detail links. Start opens each link, clicks **APPLY WITH AUTOFILL** or **EASY APPLY**, and follows the resulting application site or dialog. Delayed new tabs and redirects are associated with that job. After the run, select a row to see its exact attention or result message below the table.

The ChatGPT extension sends form questions, the saved profile, and the matching PDF to a new conversation in your ChatGPT account, then returns structured answers. The runner uses only supplied facts, leaves unsupported required answers for attention, and does not bypass login or CAPTCHA. Custom employer workflows and ambiguous one-click submission controls on third-party sites may need manual attention.

The app records confirmed and uncertain final submissions in `application-history.json` to avoid automatic duplicate applications on restart. Review an uncertain application's browser tab before taking further action. The complete setup and compatibility details are in **AUTOMATION.md** in the downloadable package.
