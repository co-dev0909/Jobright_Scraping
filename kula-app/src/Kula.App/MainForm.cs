using Kula.Bridge;

namespace Kula.App;

internal sealed class MainForm : Form
{
    private readonly ListBox jobLinks = new()
    {
        Dock = DockStyle.Fill,
        HorizontalScrollbar = true,
        IntegralHeight = false,
        Font = new Font("Segoe UI", 10),
    };
    private readonly Label jobCount = new() { Text = "0 job links", AutoSize = true };
    private readonly Label connectionStatus = new()
    {
        Text = "Starting local connection…",
        AutoSize = true,
        ForeColor = Color.DimGray,
    };
    private readonly Label transferStatus = new()
    {
        Text = "Waiting for job links. The next received batch will replace this list.",
        AutoSize = true,
        ForeColor = Color.DimGray,
    };
    private readonly Button copyKey = new()
    {
        Text = "Copy pairing key",
        AutoSize = true,
        Enabled = false,
        Padding = new Padding(8, 4, 8, 4),
    };
    private readonly CancellationTokenSource lifetime = new();
    private BridgeServer? bridge;
    private Task? startupTask;
    private string? pairingKey;
    private bool closing;
    private bool shutdownComplete;

    public MainForm()
    {
        Text = "Kula APP";
        StartPosition = FormStartPosition.CenterScreen;
        ClientSize = new Size(900, 650);
        MinimumSize = new Size(720, 540);
        AutoScaleMode = AutoScaleMode.Dpi;

        var layout = new TableLayoutPanel
        {
            Dock = DockStyle.Fill,
            Padding = new Padding(22),
            ColumnCount = 1,
            RowCount = 8,
        };
        layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        for (int row = 0; row < 6; row++) layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));

        layout.Controls.Add(new Label
        {
            Text = "Kula APP",
            Font = new Font("Segoe UI", 23, FontStyle.Bold),
            AutoSize = true,
            Margin = new Padding(0, 0, 0, 8),
        }, 0, 0);
        layout.Controls.Add(new Label
        {
            Text = "Receive filtered job links from your browser extension.",
            AutoSize = true,
            Margin = new Padding(0, 0, 0, 15),
        }, 0, 1);
        connectionStatus.Margin = new Padding(0, 0, 0, 12);
        layout.Controls.Add(connectionStatus, 0, 2);
        copyKey.Margin = new Padding(0, 0, 0, 10);
        layout.Controls.Add(copyKey, 0, 3);
        layout.Controls.Add(new Label
        {
            Text = "First time: copy the pairing key into the Jobright extension’s Kula APP settings.\n" +
                   "Then open your filtered Jobright list and click “Send to Kula APP”. Keep this app open.",
            AutoSize = true,
            Margin = new Padding(0, 0, 0, 20),
        }, 0, 4);
        jobCount.Margin = new Padding(0, 0, 0, 8);
        layout.Controls.Add(jobCount, 0, 5);
        jobLinks.Margin = new Padding(0, 0, 0, 12);
        layout.Controls.Add(jobLinks, 0, 6);
        transferStatus.Margin = Padding.Empty;
        layout.Controls.Add(transferStatus, 0, 7);
        Controls.Add(layout);

        copyKey.Click += CopyPairingKey;
        Shown += async (_, _) =>
        {
            startupTask = StartBridgeAsync();
            await startupTask;
        };
        FormClosing += CloseAfterBridgeStops;
    }

    private async Task StartBridgeAsync()
    {
        try
        {
            pairingKey = PairingKeyStore.LoadOrCreate();
            bridge = new BridgeServer(pairingKey, ReplaceJobLinksAsync);
            await bridge.StartAsync(lifetime.Token);
            if (closing) return;
            connectionStatus.Text = "Connected locally: 127.0.0.1:43127";
            connectionStatus.ForeColor = Color.FromArgb(8, 112, 76);
            copyKey.Enabled = true;
        }
        catch (OperationCanceledException) when (closing)
        {
            // Closing while startup is in progress is an ordinary shutdown.
        }
        catch (InvalidDataException)
        {
            if (closing) return;
            connectionStatus.Text = "The saved pairing key is invalid. Kula APP could not start.";
            connectionStatus.ForeColor = Color.Firebrick;
            transferStatus.Text = "Close Kula APP and remove the invalid pairing-key.txt in %LOCALAPPDATA%\\KulaAPP, then pair again.";
        }
        catch (Exception)
        {
            if (closing) return;
            connectionStatus.Text = "Could not start the local connection on 127.0.0.1:43127.";
            connectionStatus.ForeColor = Color.Firebrick;
            transferStatus.Text = "Close another copy of Kula APP if it is running. Also check access to %LOCALAPPDATA%\\KulaAPP, then reopen this app.";
        }
    }

    private async Task ReplaceJobLinksAsync(IReadOnlyList<string> links)
    {
        if (closing || IsDisposed || !IsHandleCreated)
            throw new OperationCanceledException("Kula APP is closing.");

        string[] replacement = links.ToArray();
        var completion = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        using CancellationTokenRegistration cancellation = lifetime.Token.Register(() =>
            completion.TrySetCanceled(lifetime.Token));

        void UpdateList()
        {
            if (closing || IsDisposed)
            {
                completion.TrySetCanceled();
                return;
            }

            object[] original = jobLinks.Items.Cast<object>().ToArray();
            jobLinks.BeginUpdate();
            try
            {
                jobLinks.Items.Clear();
                jobLinks.Items.AddRange(replacement);
                jobCount.Text = $"{replacement.Length:N0} job link{(replacement.Length == 1 ? "" : "s")}";
                transferStatus.Text = "Received from your extension. The previous list was replaced.";
                transferStatus.ForeColor = Color.FromArgb(8, 112, 76);
                completion.TrySetResult();
            }
            catch (Exception error)
            {
                jobLinks.Items.Clear();
                jobLinks.Items.AddRange(original);
                completion.TrySetException(error);
            }
            finally
            {
                jobLinks.EndUpdate();
            }
        }

        if (InvokeRequired) BeginInvoke((Action)UpdateList);
        else UpdateList();
        await completion.Task;
    }

    private void CopyPairingKey(object? sender, EventArgs args)
    {
        if (pairingKey is null || closing) return;
        try
        {
            Clipboard.SetText(pairingKey);
            transferStatus.Text = "Pairing key copied. Paste it into the extension’s Kula APP settings.";
            transferStatus.ForeColor = Color.DimGray;
        }
        catch (System.Runtime.InteropServices.ExternalException)
        {
            transferStatus.Text = "The clipboard is busy. Click “Copy pairing key” again.";
            transferStatus.ForeColor = Color.Firebrick;
        }
    }

    private async void CloseAfterBridgeStops(object? sender, FormClosingEventArgs args)
    {
        if (shutdownComplete) return;
        args.Cancel = true;
        if (closing) return;
        closing = true;
        copyKey.Enabled = false;
        connectionStatus.Text = "Stopping local connection…";
        lifetime.Cancel();

        try
        {
            if (startupTask is not null) await startupTask;
            if (bridge is not null) await bridge.DisposeAsync();
        }
        catch (Exception)
        {
            // The form must still close if the local listener fails to stop.
            // Closing the application also releases its loopback socket.
        }
        finally
        {
            shutdownComplete = true;
            lifetime.Dispose();
            Close();
        }
    }
}
