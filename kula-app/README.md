# Kula APP for Windows

Kula APP receives job links from the Jobright extension and displays them in a Windows list box. Each accepted batch **replaces the entire previous list**. The list is held in memory and starts empty when you reopen the app.

## Download and run

In the repository's GitHub **Actions** tab, open the latest successful **Build Kula APP for Windows** run and download **KulaAPP-windows-x64** from its artifacts. Extract the ZIP, run **KulaAPP.exe**, and load the included **extension** folder at `chrome://extensions`. The executable includes its .NET runtime; no SDK installation is needed to run it.

Artifacts become available only after the workflow finishes successfully. They expire after 30 days; a new run creates a fresh package. If GitHub Actions is disabled or you prefer to build locally, use the steps below.

## Build locally

Install the [.NET 10 SDK](https://dotnet.microsoft.com/download/dotnet/10.0) to build the app. From PowerShell in this repository:

```powershell
./kula-app/publish-windows.ps1
./kula-app/artifacts/win-x64/KulaAPP.exe
```

If local PowerShell policy prevents running a script, use the equivalent build command directly:

```powershell
dotnet publish ./kula-app/src/Kula.App/Kula.App.csproj -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true -o ./kula-app/artifacts/win-x64
```

The published Windows x64 executable includes its .NET runtime. The SDK is needed to build it, but is not needed on a computer that only runs the published executable. No administrator setup or browser native-host registration is required.

## Connect the extension

1. Keep Kula APP open on the same Windows computer as your browser.
2. Click **Copy pairing key** in Kula APP and save that key in the extension's Kula APP settings. Pairing is needed once per browser profile.
3. Open your filtered Jobright list and click **Send to Kula APP** in the extension popup. Keep the popup open while it collects links.
4. When the transfer succeeds, the old Kula APP list is replaced with the new links. Invalid requests and failed collections leave the previous list intact. If a send times out or its reply is lost, check the app's list before retrying: the replacement may already have completed.

The local receiver listens on `127.0.0.1:43127` and requires the pairing key for transfers. The key is saved in `%LOCALAPPDATA%\KulaAPP\pairing-key.txt`, and stays the same after restarting the app. Treat the key as a local credential; do not commit or share it.

If the app cannot start its connection, close another running copy of Kula APP and reopen it. Another program using port `43127` can also prevent startup. If a saved key is invalid, close the app, remove the invalid key file, reopen the app, and pair the extension again.

The second extension is outside this first feature. It can use the same local connection protocol when its workflow is defined.
