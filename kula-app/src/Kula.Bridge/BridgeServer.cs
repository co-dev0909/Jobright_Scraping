using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;

namespace Kula.Bridge;

/// <summary>Authenticated, loopback-only link replacement for Kula APP.</summary>
public sealed partial class BridgeServer : IAsyncDisposable
{
    public const int DefaultPort = 43127;
    public const int MaxLinks = 10_000;
    public const int MaxBodyBytes = 1_048_576;
    private readonly byte[] expectedKey;
    private readonly Func<IReadOnlyList<string>, Task> replaceLinks;
    private readonly int port;
    private readonly SemaphoreSlim replacementLock = new(1, 1);
    private WebApplication? application;

    public BridgeServer(string pairingKey, Func<IReadOnlyList<string>, Task> replaceLinks, int port = DefaultPort)
    {
        if (!PairingKeyPattern().IsMatch(pairingKey))
            throw new ArgumentException("The pairing key must contain 64 hexadecimal characters.", nameof(pairingKey));
        if (port is < 1 or > 65535) throw new ArgumentOutOfRangeException(nameof(port));
        expectedKey = Encoding.ASCII.GetBytes(pairingKey.ToLowerInvariant());
        this.replaceLinks = replaceLinks ?? throw new ArgumentNullException(nameof(replaceLinks));
        this.port = port;
    }

    public async Task StartAsync(CancellationToken cancellationToken = default)
    {
        if (application is not null) throw new InvalidOperationException("The bridge has already started.");
        var builder = WebApplication.CreateSlimBuilder(new WebApplicationOptions
        {
            Args = [],
            EnvironmentName = Environments.Production,
            ContentRootPath = AppContext.BaseDirectory,
        });
        // External URL/settings overrides must never expose this local bridge.
        builder.Configuration.Sources.Clear();
        builder.Logging.ClearProviders();
        builder.WebHost.UseKestrel(options =>
        {
            options.Listen(IPAddress.Loopback, port);
            options.Limits.MaxRequestBodySize = MaxBodyBytes;
            options.Limits.RequestHeadersTimeout = TimeSpan.FromSeconds(10);
        });
        var app = builder.Build();
        app.MapPost("/v1/job-links", (Func<HttpContext, Task<IResult>>)HandleReplacementAsync);
        app.MapFallback(() => Error("Unknown Kula APP endpoint.", StatusCodes.Status404NotFound));
        try
        {
            await app.StartAsync(cancellationToken);
            application = app;
        }
        catch
        {
            await app.DisposeAsync();
            throw;
        }
    }

    private async Task<IResult> HandleReplacementAsync(HttpContext context)
    {
        if (!string.Equals(context.Request.Host.Value, $"127.0.0.1:{port}", StringComparison.Ordinal))
            return Error("Invalid local destination.", StatusCodes.Status403Forbidden);
        var origin = context.Request.Headers.Origin.ToString();
        if (origin.Length > 0 && !ExtensionOriginPattern().IsMatch(origin))
            return Error("Only paired browser extensions can send job links.", StatusCodes.Status403Forbidden);
        var authorization = context.Request.Headers.Authorization.ToString();
        if (!authorization.StartsWith("Bearer ", StringComparison.Ordinal) ||
            !CryptographicOperations.FixedTimeEquals(
                Encoding.ASCII.GetBytes(authorization[7..].ToLowerInvariant()), expectedKey))
            return Error("Pairing key rejected. Copy the key from Kula APP and save it in the extension.", StatusCodes.Status401Unauthorized);
        if (!context.Request.HasJsonContentType())
            return Error("Send a JSON job-link batch.", StatusCodes.Status415UnsupportedMediaType);
        if (context.Request.ContentLength > MaxBodyBytes)
            return Error("The link batch is too large.", StatusCodes.Status413PayloadTooLarge);

        IReadOnlyList<string> links;
        try
        {
            using var body = await JsonDocument.ParseAsync(context.Request.Body,
                new JsonDocumentOptions { MaxDepth = 8 }, context.RequestAborted);
            links = ValidateBatch(body.RootElement);
        }
        catch (BadHttpRequestException error) when (error.StatusCode == StatusCodes.Status413PayloadTooLarge)
        {
            return Error("The link batch is too large.", StatusCodes.Status413PayloadTooLarge);
        }
        catch (Exception error) when (error is JsonException or ArgumentException)
        {
            return Error("Send protocolVersion 1 and between 1 and 10000 valid HTTPS Jobright job links.", StatusCodes.Status400BadRequest);
        }

        // Serialize whole replacements. Validation has completed before the UI
        // receives anything, and success is acknowledged only after UI update.
        await replacementLock.WaitAsync(context.RequestAborted);
        try
        {
            // Once a replacement starts, keep its lock until it actually ends.
            // A disconnected sender must not let a second batch overtake a
            // callback that is still updating the desktop UI.
            await replaceLinks(links);
            return Results.Json(new { ok = true, count = links.Count });
        }
        catch (OperationCanceledException) when (context.RequestAborted.IsCancellationRequested)
        {
            throw;
        }
        catch
        {
            return Error("Kula APP could not update its job list. Keep the app open and try again.", StatusCodes.Status500InternalServerError);
        }
        finally
        {
            replacementLock.Release();
        }
    }

    private static IReadOnlyList<string> ValidateBatch(JsonElement body)
    {
        if (body.ValueKind != JsonValueKind.Object ||
            !body.TryGetProperty("protocolVersion", out var version) || version.ValueKind != JsonValueKind.Number ||
            !version.TryGetInt32(out var number) || number != 1 ||
            !body.TryGetProperty("links", out var array) || array.ValueKind != JsonValueKind.Array ||
            array.GetArrayLength() is < 1 or > MaxLinks)
            throw new ArgumentException("Invalid job-link batch.");

        var links = new List<string>();
        var seen = new HashSet<string>(StringComparer.Ordinal);
        foreach (var entry in array.EnumerateArray())
        {
            if (entry.ValueKind != JsonValueKind.String) throw new ArgumentException("Invalid job link.");
            var text = entry.GetString()!;
            if (text.Length > 2048 || !Uri.TryCreate(text, UriKind.Absolute, out var url) ||
                url.Scheme != Uri.UriSchemeHttps || !url.IsDefaultPort || url.UserInfo.Length != 0 ||
                (url.Host != "jobright.ai" && url.Host != "www.jobright.ai"))
                throw new ArgumentException("Invalid job link.");
            var match = JobPathPattern().Match(url.AbsolutePath);
            if (!match.Success) throw new ArgumentException("Invalid job link.");
            var id = match.Groups[1].Value;
            if (HexIdPattern().IsMatch(id)) id = id.ToLowerInvariant();
            var canonical = $"https://jobright.ai/jobs/info/{id}";
            if (seen.Add(canonical)) links.Add(canonical);
        }
        return links.AsReadOnly();
    }

    private static IResult Error(string message, int status) => Results.Json(new { error = message }, statusCode: status);

    public async ValueTask DisposeAsync()
    {
        if (application is null) return;
        var app = application;
        application = null;
        using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        try { await app.StopAsync(deadline.Token); }
        finally { await app.DisposeAsync(); }
    }

    [GeneratedRegex("\\A[0-9a-fA-F]{64}\\z")]
    private static partial Regex PairingKeyPattern();
    [GeneratedRegex("\\Achrome-extension://[a-p]{32}\\z")]
    private static partial Regex ExtensionOriginPattern();
    [GeneratedRegex("\\A/jobs/info/([A-Za-z0-9_-]{1,200})/?\\z")]
    private static partial Regex JobPathPattern();
    [GeneratedRegex("\\A[a-fA-F0-9]{24}\\z")]
    private static partial Regex HexIdPattern();
}
