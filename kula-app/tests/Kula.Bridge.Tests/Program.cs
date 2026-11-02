using System.Net;
using System.Net.Http.Headers;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using Kula.Bridge;

var tests = new (string Name, Func<Task> Run)[]
{
    ("valid batches replace the previous list and canonicalize unique URLs", Replacement),
    ("invalid batches never replace the existing list", InvalidBatches),
    ("authentication and browser-origin checks preserve the existing list", AccessControls),
    ("HTTP body and route validation reject unsupported requests", RequestLimits),
    ("success waits for the UI replacement callback to finish", Acknowledgement),
    ("disconnecting a sender does not allow overlapping replacements", DisconnectedSender),
    ("replacement failures return a generic error", CallbackFailure),
};

var failed = 0;
foreach (var test in tests)
{
    try
    {
        await test.Run();
        Console.WriteLine($"PASS {test.Name}");
    }
    catch (Exception error)
    {
        failed++;
        Console.Error.WriteLine($"FAIL {test.Name}: {error.Message}");
    }
}
Console.WriteLine($"{tests.Length - failed}/{tests.Length} integration tests passed.");
return failed == 0 ? 0 : 1;

static async Task Replacement()
{
    await using var fixture = await Fixture.Start();
    using var first = await fixture.Send(new
    {
        protocolVersion = 1,
        links = new[]
        {
            "https://www.jobright.ai/jobs/info/ABCDEF0123456789ABCDEF01/?source=recommend#apply",
            "https://jobright.ai/jobs/info/abcdef0123456789abcdef01",
            "https://jobright.ai/jobs/info/second",
        },
    });
    await ExpectSuccess(first, 2);
    Equal(fixture.Links, new[]
    {
        "https://jobright.ai/jobs/info/abcdef0123456789abcdef01",
        "https://jobright.ai/jobs/info/second",
    }, "URLs must be canonical and deduplicated in first-seen order");
    using var second = await fixture.Send(new { protocolVersion = 1, links = new[] { Fixture.NewLink } });
    await ExpectSuccess(second, 1);
    Equal(fixture.Links, new[] { Fixture.NewLink }, "The second batch must replace, rather than append to, the first");
    Check(fixture.ReplacementCount == 2, "Each accepted batch must update the app exactly once");
}

static async Task InvalidBatches()
{
    await using var fixture = await Fixture.Start();
    var invalidUrls = new[]
    {
        "http://jobright.ai/jobs/info/insecure",
        "https://example.com/jobs/info/offsite",
        "https://jobright.ai.example.com/jobs/info/spoofed",
        "https://user:password@jobright.ai/jobs/info/credentials",
        "https://jobright.ai:444/jobs/info/port",
        "https://jobright.ai/jobs/info/extra/path",
        "https://jobright.ai/jobs/info/encoded%2Fslash",
        "https://jobright.ai/jobs/info/encoded%5Cslash",
        "https://jobright.ai/jobs/info/",
        "javascript:alert(1)",
        "not a URL",
        "https://jobright.ai/jobs/info/" + new string('a', 2050),
    };
    foreach (var invalid in invalidUrls)
    {
        using var response = await fixture.Send(new { protocolVersion = 1, links = new[] { Fixture.NewLink, invalid } });
        Status(response, HttpStatusCode.BadRequest);
        fixture.ExpectUnchanged();
    }
    foreach (var payload in new object[]
    {
        new { protocolVersion = 1, links = Array.Empty<string>() },
        new { protocolVersion = 2, links = new[] { Fixture.NewLink } },
        new { links = new[] { Fixture.NewLink } },
        new { protocolVersion = 1, links = new string?[] { Fixture.NewLink, null } },
        new { protocolVersion = 1, links = "not an array" },
        new { protocolVersion = 1, links = Enumerable.Repeat(Fixture.NewLink, 10001).ToArray() },
    })
    {
        using var response = await fixture.Send(payload);
        Status(response, HttpStatusCode.BadRequest);
        fixture.ExpectUnchanged();
    }
    using var malformed = await fixture.SendRaw("{\"protocolVersion\":1,\"links\":[");
    Status(malformed, HttpStatusCode.BadRequest);
    fixture.ExpectUnchanged();
}

static async Task AccessControls()
{
    await using var fixture = await Fixture.Start();
    using (var response = await fixture.SendBatch(token: null)) Status(response, HttpStatusCode.Unauthorized);
    fixture.ExpectUnchanged();
    using (var response = await fixture.SendBatch(token: new string('b', 64))) Status(response, HttpStatusCode.Unauthorized);
    fixture.ExpectUnchanged();
    foreach (var origin in new[] { "https://jobright.ai", "http://127.0.0.1:43127", "null", "chrome-extension://" + new string('x', 32) })
    {
        using var response = await fixture.SendBatch(origin: origin);
        Status(response, HttpStatusCode.Forbidden);
        fixture.ExpectUnchanged();
    }
    using (var request = fixture.Request(JsonSerializer.Serialize(new { protocolVersion = 1, links = new[] { Fixture.NewLink } })))
    {
        request.Headers.Host = $"localhost:{fixture.Port}";
        using var response = await fixture.Client.SendAsync(request);
        Check(response.StatusCode is HttpStatusCode.BadRequest or HttpStatusCode.Forbidden, "An unexpected Host must be rejected");
    }
    fixture.ExpectUnchanged();
    using var allowed = await fixture.SendBatch(origin: "chrome-extension://" + new string('a', 32));
    await ExpectSuccess(allowed, 1);
    Equal(fixture.Links, new[] { Fixture.NewLink }, "A paired extension origin should be accepted");
}

static async Task RequestLimits()
{
    await using var fixture = await Fixture.Start();
    using (var response = await fixture.SendRaw("{}", contentType: "text/plain")) Status(response, HttpStatusCode.UnsupportedMediaType);
    fixture.ExpectUnchanged();
    using (var request = fixture.Request(new string(' ', 1024 * 1024 + 100)))
    {
        // Wait for the server's headers-only size rejection instead of racing
        // a large upload against Kestrel closing the rejected request body.
        request.Headers.ExpectContinue = true;
        using var response = await fixture.Client.SendAsync(request);
        Status(response, HttpStatusCode.RequestEntityTooLarge);
    }
    fixture.ExpectUnchanged();
    using (var request = fixture.Request("{}", route: "/v1/not-a-route"))
    using (var response = await fixture.Client.SendAsync(request)) Status(response, HttpStatusCode.NotFound);
    fixture.ExpectUnchanged();
}

static async Task Acknowledgement()
{
    var entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    var release = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    await using var fixture = await Fixture.Start(async _ =>
    {
        entered.SetResult();
        await release.Task;
    });
    var pending = fixture.SendBatch();
    await entered.Task.WaitAsync(TimeSpan.FromSeconds(5));
    await Task.Delay(50);
    Check(!pending.IsCompleted, "The app must not acknowledge before its list is replaced");
    release.SetResult();
    using var response = await pending;
    await ExpectSuccess(response, 1);
}

static async Task CallbackFailure()
{
    await using var fixture = await Fixture.Start(_ => throw new InvalidOperationException("private replacement details"));
    using var response = await fixture.SendBatch();
    Status(response, HttpStatusCode.InternalServerError);
    var body = await response.Content.ReadAsStringAsync();
    Check(!body.Contains("private replacement details", StringComparison.Ordinal), "Response must not expose callback details");
    Check(body.Contains("error", StringComparison.OrdinalIgnoreCase), "A failed update must return an error response");
    fixture.ExpectUnchanged();
}

static async Task DisconnectedSender()
{
    var entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    var release = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    var secondEntered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    var calls = 0;
    await using var fixture = await Fixture.Start(async _ =>
    {
        if (Interlocked.Increment(ref calls) == 1)
        {
            entered.SetResult();
            await release.Task;
        }
        else secondEntered.SetResult();
    });
    using var cancellation = new CancellationTokenSource(TimeSpan.FromSeconds(10));
    using var request = fixture.Request(JsonSerializer.Serialize(new { protocolVersion = 1, links = new[] { Fixture.NewLink } }));
    var first = fixture.Client.SendAsync(request, cancellation.Token);
    try
    {
        await entered.Task.WaitAsync(TimeSpan.FromSeconds(5));
        cancellation.Cancel();
        try
        {
            using var unexpected = await first;
            throw new InvalidOperationException("The waiting sender should have disconnected");
        }
        catch (OperationCanceledException) { }

        var second = fixture.SendBatch();
        await Task.Delay(250);
        Check(!secondEntered.Task.IsCompleted, "The bridge must hold its replacement lock until the first UI callback finishes, even after its sender disconnects");
        release.SetResult();
        using var response = await second;
        await ExpectSuccess(response, 1);
        Check(calls == 2, "Both accepted replacements must complete exactly once");
    }
    finally { release.TrySetResult(); }
}

static async Task ExpectSuccess(HttpResponseMessage response, int expectedCount)
{
    Status(response, HttpStatusCode.OK);
    using var body = JsonDocument.Parse(await response.Content.ReadAsStringAsync());
    Check(body.RootElement.GetProperty("ok").GetBoolean(), "Response must acknowledge success");
    Check(body.RootElement.GetProperty("count").GetInt32() == expectedCount, "Response count must match the committed unique links");
}

static void Status(HttpResponseMessage response, HttpStatusCode expected) =>
    Check(response.StatusCode == expected, $"Expected HTTP {(int)expected}, received {(int)response.StatusCode}");

static void Equal(IEnumerable<string> actual, IEnumerable<string> expected, string message) =>
    Check(actual.SequenceEqual(expected), message);

static void Check(bool condition, string message)
{
    if (!condition) throw new InvalidOperationException(message);
}

sealed class Fixture : IAsyncDisposable
{
    public const string OldLink = "https://jobright.ai/jobs/info/previous";
    public const string NewLink = "https://jobright.ai/jobs/info/current";
    private const string PairingKey = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    private readonly BridgeServer server;
    public HttpClient Client { get; } = new(new HttpClientHandler { UseProxy = false }) { Timeout = TimeSpan.FromSeconds(15) };
    public int Port { get; }
    public IReadOnlyList<string> Links { get; private set; } = new[] { OldLink };
    public int ReplacementCount { get; private set; }

    private Fixture(int port, Func<IReadOnlyList<string>, Task>? replace)
    {
        Port = port;
        server = new BridgeServer(PairingKey, replace ?? (links =>
        {
            Links = links.ToArray();
            ReplacementCount++;
            return Task.CompletedTask;
        }), port);
    }

    public static async Task<Fixture> Start(Func<IReadOnlyList<string>, Task>? replace = null)
    {
        var probe = new TcpListener(IPAddress.Loopback, 0);
        probe.Start();
        var port = ((IPEndPoint)probe.LocalEndpoint).Port;
        probe.Stop();
        var fixture = new Fixture(port, replace);
        try
        {
            await fixture.server.StartAsync();
            return fixture;
        }
        catch
        {
            await fixture.DisposeAsync();
            throw;
        }
    }

    public Task<HttpResponseMessage> Send(object payload) => SendRaw(JsonSerializer.Serialize(payload));

    public Task<HttpResponseMessage> SendBatch(string? token = PairingKey, string? origin = null) =>
        SendRaw(JsonSerializer.Serialize(new { protocolVersion = 1, links = new[] { NewLink } }), token, origin);

    public async Task<HttpResponseMessage> SendRaw(string json, string? token = PairingKey, string? origin = null, string contentType = "application/json")
    {
        using var request = Request(json, token, origin, contentType);
        return await Client.SendAsync(request);
    }

    public HttpRequestMessage Request(string json, string? token = PairingKey, string? origin = null, string contentType = "application/json", string route = "/v1/job-links")
    {
        var request = new HttpRequestMessage(HttpMethod.Post, $"http://127.0.0.1:{Port}{route}")
        {
            Content = new StringContent(json, Encoding.UTF8, contentType),
        };
        if (token is not null) request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        if (origin is not null) request.Headers.Add("Origin", origin);
        return request;
    }

    public void ExpectUnchanged()
    {
        if (!Links.SequenceEqual(new[] { OldLink }) || ReplacementCount != 0)
            throw new InvalidOperationException("A rejected request must retain the previous list without invoking replacement");
    }

    public async ValueTask DisposeAsync()
    {
        Client.Dispose();
        await server.DisposeAsync();
    }
}
