using System.Net;
using System.Text.Json;
using System.Text.RegularExpressions;
using Microsoft.Data.Sqlite;
using TodoApp.Cli;
using TodoApp.Infrastructure;
using Xunit;

namespace TodoApp.Tests;

[Collection(WebHostSerial.Name)]
public sealed class WebHostTests
{
    private static Uri U(string path) => new(path, UriKind.Relative);

    private static CancellationToken Ct => TestContext.Current.CancellationToken;

    [Fact]
    public async Task 応答_未定義パスでも_セキュリティヘッダーとno_storeが付きHSTSは無い()
    {
        await using var app = await TestWebApp.CreateAsync();
        using var client = app.CreateClient();
        using var response = await client.GetAsync(U("/"), Ct);

        Assert.Equal(
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
            string.Join(",", response.Headers.GetValues("Content-Security-Policy")));
        Assert.Equal("nosniff", string.Join(",", response.Headers.GetValues("X-Content-Type-Options")));
        Assert.Equal("no-referrer", string.Join(",", response.Headers.GetValues("Referrer-Policy")));
        Assert.True(response.Headers.CacheControl?.NoStore);
        Assert.False(response.Headers.Contains("Strict-Transport-Security"));
    }

    [Fact]
    public async Task Healthz_ループバックのHTTPS_200()
    {
        await using var app = await TestWebApp.CreateAsync();
        using var client = app.CreateClient();
        using var response = await client.GetAsync(U("/healthz"), Ct);
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    }

    [Fact]
    public async Task Healthz_ループバックのHTTP_転送せず200()
    {
        await using var app = await TestWebApp.CreateAsync();
        using var client = app.CreateClient(https: false);
        using var response = await client.GetAsync(U("/healthz"), Ct);
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    }

    [Fact]
    public async Task Healthz_ループバック以外_404()
    {
        await using var app = await TestWebApp.CreateAsync();
        using var client = app.CreateClient();
        using var request = new HttpRequestMessage(HttpMethod.Get, U("/healthz"));
        request.Headers.Add(TestWebApp.RemoteIpHeader, "192.168.1.10");
        using var response = await client.SendAsync(request, Ct);
        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);
    }

    [Fact]
    public async Task Healthz_起動後にDBの版が合わない_503()
    {
        await using var app = await TestWebApp.CreateAsync();
        using var client = app.CreateClient();
        using (var first = await client.GetAsync(U("/healthz"), Ct))
        {
            Assert.Equal(HttpStatusCode.OK, first.StatusCode);
        }

        SqliteConnection.ClearAllPools();
        File.Delete(Path.Combine(app.DataDirectory, CliRunner.DatabaseFileName));
        using var response = await client.GetAsync(U("/healthz"), Ct);
        Assert.Equal(HttpStatusCode.ServiceUnavailable, response.StatusCode);
    }

    [Theory]
    [InlineData("127.0.0.1", "/tasks?q=a")]
    [InlineData("192.168.1.10", "/healthz")]
    public async Task HTTP要求_転送対象_301でポート無しのHTTPSへ(string remoteIp, string path)
    {
        await using var app = await TestWebApp.CreateAsync();
        using var client = app.CreateClient(https: false);
        using var request = new HttpRequestMessage(HttpMethod.Get, U(path));
        request.Headers.Add(TestWebApp.RemoteIpHeader, remoteIp);
        using var response = await client.SendAsync(request, Ct);
        Assert.Equal(HttpStatusCode.MovedPermanently, response.StatusCode);
        Assert.Equal(new Uri("https://localhost" + path), response.Headers.Location);
    }

    [Fact]
    public async Task 想定外例外_画面_500で相関IDを出しスタックトレースを出さない()
    {
        await using var app = await TestWebApp.CreateAsync();
        using var client = await app.CreateLoggedInClientAsync();
        using var response = await client.GetAsync(U(TestWebApp.ThrowPath), Ct);
        var body = await response.Content.ReadAsStringAsync(Ct);

        Assert.Equal(HttpStatusCode.InternalServerError, response.StatusCode);
        Assert.Equal("text/html", response.Content.Headers.ContentType?.MediaType);
        Assert.Contains(ErrorIds.SysUnexpected, body, StringComparison.Ordinal);
        Assert.Matches(@"相関 ID: <code>[^<\s]+</code>", body);
        Assert.DoesNotContain(TestWebApp.ThrowMarker, body, StringComparison.Ordinal);
        Assert.DoesNotContain("InvalidOperationException", body, StringComparison.Ordinal);
        Assert.DoesNotContain(" at ", body, StringComparison.Ordinal);
        Assert.Contains("default-src 'self'", string.Join(",", response.Headers.GetValues("Content-Security-Policy")), StringComparison.Ordinal);
        Assert.True(response.Headers.CacheControl?.NoStore);
    }

    [Fact]
    public async Task 想定外例外_API_JSONのエラー形で500()
    {
        await using var app = await TestWebApp.CreateAsync();
        using var client = await app.CreateLoggedInClientAsync();
        using var response = await client.GetAsync(U("/api" + TestWebApp.ThrowPath), Ct);
        var body = await response.Content.ReadAsStringAsync(Ct);

        Assert.Equal(HttpStatusCode.InternalServerError, response.StatusCode);
        using var json = JsonDocument.Parse(body);
        var error = json.RootElement.GetProperty("error");
        Assert.Equal(ErrorIds.SysUnexpected, error.GetProperty("id").GetString());
        Assert.NotEmpty(error.GetProperty("message").GetString()!);
        Assert.DoesNotContain(TestWebApp.ThrowMarker, body, StringComparison.Ordinal);
    }

    [Fact]
    public async Task 想定外例外_ログファイルに相関IDが出る()
    {
        await using var app = await TestWebApp.CreateAsync();
        string correlationId;
        using (var client = await app.CreateLoggedInClientAsync())
        using (var response = await client.GetAsync(U(TestWebApp.ThrowPath), Ct))
        {
            var body = await response.Content.ReadAsStringAsync(Ct);
            correlationId = Regex.Match(body, "<code>([^<]+)</code>").Groups[1].Value;
        }

        await app.StopAsync();
        var logFiles = Directory.GetFiles(Path.Combine(app.DataDirectory, "logs"), "app-*.log");
        Assert.Matches(@"app-\d{8}\.log$", Assert.Single(logFiles));
        Assert.NotEmpty(correlationId);
        Assert.Contains(correlationId, await File.ReadAllTextAsync(logFiles[0], Ct), StringComparison.Ordinal);
    }

    [Fact]
    public async Task 要求ログ_tasks以外のクエリ文字列_記録しない()
    {
        await using var app = await TestWebApp.CreateAsync();
        using (var client = app.CreateClient())
        {
            using var a = await client.GetAsync(U("/login?password=SECRETVALUE"), Ct);
            using var b = await client.GetAsync(U("/tasks?q=searchword"), Ct);
        }

        await app.StopAsync();
        var log = await File.ReadAllTextAsync(Directory.GetFiles(Path.Combine(app.DataDirectory, "logs"), "app-*.log")[0], Ct);
        Assert.DoesNotContain("SECRETVALUE", log, StringComparison.Ordinal);
        Assert.Contains("q=searchword", log, StringComparison.Ordinal);
    }
}
