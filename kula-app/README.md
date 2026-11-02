# Kula APP for Windows

Kula APP receives job links from the Jobright extension and displays them in a Windows list box. Each accepted batch **replaces the entire previous list**. The list is held in memory and starts empty when you reopen the app.

## Download and run

In the repository's GitHub **Actions** tab, open the latest successful **Build Kula APP for Windows** run and download **KulaAPP-windows-x64** from its artifacts. Extract the ZIP, run **KulaAPP.exe**, and load the included **extension** folder at `chrome://extensions`.

The app is now written in Python. The single executable bundles Python and Tcl/Tk, so **you do not need to install Python, .NET, or any SDK to run it**. No administrator setup is required. Existing extension connections and saved pairing keys continue to work.

Artifacts become available only after the workflow finishes successfully. They expire after 30 days; a new run creates a fresh package. If GitHub Actions is disabled or you prefer to build locally, use the steps below.

## About the executable

GitHub Actions builds the executable from this repository, tests the receiver and UI, and smoke-tests the packaged executable before uploading it. The package includes **SHA256SUMS.txt** for checking the executable's hash. This is an unsigned app: Windows may show an unknown-publisher or SmartScreen warning. Bundling Python does not provide code signing or a guarantee that Windows will trust the app. Do not disable Windows security protections to run it.

The list stays in memory. The app writes only its pairing key under `%LOCALAPPDATA%\KulaAPP` and listens for authenticated transfers on this computer. PyInstaller's one-file executable extracts its bundled runtime to a temporary directory while it runs.

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

Run the receiver/pairing tests and the UI smoke test:

```powershell
python -m unittest discover -s kula-app/tests -p "test_*.py" -v
python ./kula-app/kula_app.py --smoke-test
```

The UI smoke test needs a graphical session. Windows executables must be built on Windows; a Linux build produces a Linux executable.

## Connect the extension

1. Keep Kula APP open on the same Windows computer as your browser.
2. Click **Copy pairing key** in Kula APP and save that key in the extension's Kula APP settings. Pairing is needed once per browser profile.
3. Open your filtered Jobright list and click **Send to Kula APP** in the extension popup. Keep the popup open while it collects links.
4. When the transfer succeeds, the old Kula APP list is replaced with the new links. Invalid requests and failed collections leave the previous list intact. If a send times out or its reply is lost, check the app's list before retrying: the replacement may already have completed.

The local receiver listens on `127.0.0.1:43127` and requires the pairing key for transfers. The key is saved in `%LOCALAPPDATA%\KulaAPP\pairing-key.txt`, and stays the same after restarting the app. Treat the key as a local credential; do not commit or share it.

If the app cannot start its connection, close another running copy of Kula APP and reopen it. Another program using port `43127` can also prevent startup. If a saved key is invalid, close the app, remove the invalid key file, reopen the app, and pair the extension again.

The second extension is outside this first feature. It can use the same local connection protocol when its workflow is defined.
