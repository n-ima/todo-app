using System.Net;
using System.Text.Json;
using Dapper;
using Microsoft.AspNetCore.Identity;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.Filters;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Data.Sqlite;
using TodoApp.Data;
using TodoApp.Infrastructure;
using TodoApp.Services;
using Xunit;

namespace TodoApp.Tests;

/// <summary>パスワード変更と強制転送の結合テスト（TASK-103・US-002・DD-03 §3）。</summary>
[Collection(WebHostSerial.Name)]
public sealed class PasswordChangeTests
{
    private const string Initial = "initial-pw-1";
    private const string PasswordPath = "/account/password";

    private static Uri U(string path) => new(path, UriKind.Relative);

    private static CancellationToken Ct => TestContext.Current.CancellationToken;

    private static Task<HttpResponseMessage> ChangeAsync(HttpClient client, string current, string next, string confirm) =>
        TestWebApp.PostFormAsync(
            client,
            PasswordPath,
            new Dictionary<string, string> { ["currentPassword"] = current, ["newPassword"] = next, ["newPasswordConfirm"] = confirm },
            tokenPath: PasswordPath);

    private static async Task<HttpClient> LoginAsync(TestWebApp app, string loginId, string password)
    {
        var client = app.CreateClient();
        using var response = await TestWebApp.LoginAsync(client, loginId, password);
        Assert.Equal(HttpStatusCode.Redirect, response.StatusCode);
        return client;
    }

    private static async Task<(string Hash, long Mcp, string Stamp)> ReadUserAsync(TestWebApp app, long id)
    {
        await using var connection = new SqliteConnection(new SqliteConnectionStringBuilder { DataSource = app.DatabasePath }.ToString());
        return await connection.QuerySingleAsync<(string, long, string)>(
            "SELECT password_hash, must_change_password, security_stamp FROM users WHERE id = @id", new { id });
    }

    private static async Task AssertErrorAsync(HttpResponseMessage response, string errorId)
    {
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Contains($"（{errorId}）", await response.Content.ReadAsStringAsync(Ct), StringComparison.Ordinal);
    }

    [Fact]
    [Trait("TC", "US-002")]
    public async Task 強制転送_初期パスワードのまま_画面は変更画面へ302_APIは403()
    {
        await using var app = await TestWebApp.CreateAsync();
        await app.CreateUserAsync("newbie", Initial, mustChangePassword: true);
        using var client = await LoginAsync(app, "newbie", Initial);

        foreach (var path in new[] { "/", "/tasks/1", "/admin/users" })
        {
            using var page = await client.GetAsync(U(path), Ct);
            Assert.Equal(HttpStatusCode.Redirect, page.StatusCode);
            Assert.Equal(PasswordPath, page.Headers.Location?.OriginalString);
        }

        using var api = await client.GetAsync(U("/api/tasks/1/children"), Ct);
        Assert.Equal(HttpStatusCode.Forbidden, api.StatusCode);
        using (var json = JsonDocument.Parse(await api.Content.ReadAsStringAsync(Ct)))
        {
            Assert.Equal(ErrorIds.AuthMustChange, json.RootElement.GetProperty("error").GetProperty("id").GetString());
        }

        using var form = await client.GetAsync(U(PasswordPath), Ct);
        Assert.Equal(HttpStatusCode.OK, form.StatusCode);
        var body = WebUtility.HtmlDecode(await form.Content.ReadAsStringAsync(Ct));
        Assert.Contains(ErrorIds.Messages[ErrorIds.AuthMustChange], body, StringComparison.Ordinal);
        Assert.DoesNotContain("aria-label=\"メイン\"", body, StringComparison.Ordinal);

        // ログアウトは転送しない
        using var logout = await TestWebApp.PostFormAsync(client, "/logout", new Dictionary<string, string>(), tokenPath: PasswordPath);
        Assert.Equal(AuthSetup.LoginPath, logout.Headers.Location?.OriginalString);
    }

    [Fact]
    [Trait("TC", "US-002")]
    public async Task 初回変更_8文字ちょうど_フラグが落ち転送されなくなる()
    {
        await using var app = await TestWebApp.CreateAsync();
        var id = await app.CreateUserAsync("newbie", Initial, mustChangePassword: true);
        using var client = await LoginAsync(app, "newbie", Initial);

        using var response = await ChangeAsync(client, Initial, "12345678", "12345678");

        Assert.Equal(HttpStatusCode.Redirect, response.StatusCode);
        Assert.Equal("/", response.Headers.Location?.OriginalString);
        var (hash, mcp, _) = await ReadUserAsync(app, id);
        Assert.Equal(0, mcp);
        Assert.NotEqual(PasswordVerificationResult.Failed, AuthService.PasswordHasher.VerifyHashedPassword(new UserRecord(), hash, "12345678"));
        using var probe = await client.GetAsync(U("/api/probe"), Ct);
        Assert.Equal(HttpStatusCode.NotFound, probe.StatusCode);
        using var form = await client.GetAsync(U(PasswordPath), Ct);
        Assert.Contains("aria-label=\"メイン\"", WebUtility.HtmlDecode(await form.Content.ReadAsStringAsync(Ct)), StringComparison.Ordinal);

        using var other = app.CreateClient();
        using var oldLogin = await TestWebApp.LoginAsync(other, "newbie", Initial);
        Assert.Equal(HttpStatusCode.OK, oldLogin.StatusCode);
        using var newLogin = await TestWebApp.LoginAsync(other, "newbie", "12345678");
        Assert.Equal("/", newLogin.Headers.Location?.OriginalString);
    }

    [Theory]
    [Trait("TC", "US-002")]
    [InlineData("wrong-pw-99", "1234567", "x", ErrorIds.PwdCurrent)]
    [InlineData(Initial, "1234567", "1234567", ErrorIds.PwdLength)]
    [InlineData(Initial, "1234567", "different", ErrorIds.PwdLength)]
    [InlineData(Initial, "12345678", "12345679", ErrorIds.PwdConfirm)]
    [InlineData(Initial, Initial, "mismatch", ErrorIds.PwdConfirm)]
    [InlineData(Initial, Initial, Initial, ErrorIds.PwdSame)]
    public async Task 変更_検証違反_最初の違反を表示し変わらない(string current, string next, string confirm, string errorId)
    {
        await using var app = await TestWebApp.CreateAsync();
        var id = await app.CreateUserAsync("newbie", Initial, mustChangePassword: true);
        using var client = await LoginAsync(app, "newbie", Initial);
        var before = await ReadUserAsync(app, id);

        using var response = await ChangeAsync(client, current, next, confirm);

        await AssertErrorAsync(response, errorId);
        Assert.Equal(before, await ReadUserAsync(app, id));
    }

    [Fact]
    [Trait("TC", "US-002")]
    public async Task 初期パスワードのまま_想定外例外_変更画面へ転送せず500と相関ID()
    {
        await using var app = await TestWebApp.CreateAsync(s =>
            s.Configure<MvcOptions>(o => o.Filters.Add(new ThrowOnHeaderFilter())));
        await app.CreateUserAsync("newbie", Initial, mustChangePassword: true);
        using var client = await LoginAsync(app, "newbie", Initial);

        // 転送対象外の /account/password で例外を起こし、例外ハンドラの /error 再実行を通す
        using var request = new HttpRequestMessage(HttpMethod.Get, U(PasswordPath));
        request.Headers.Add(ThrowOnHeaderFilter.Header, "1");
        using var response = await client.SendAsync(request, Ct);
        var body = WebUtility.HtmlDecode(await response.Content.ReadAsStringAsync(Ct));

        Assert.Equal(HttpStatusCode.InternalServerError, response.StatusCode);
        Assert.Contains($"（{ErrorIds.SysUnexpected}）", body, StringComparison.Ordinal);
        Assert.Matches(@"相関 ID: <code>[^<\s]+</code>", body);
        Assert.DoesNotContain(TestWebApp.ThrowMarker, body, StringComparison.Ordinal);
    }

    private sealed class ThrowOnHeaderFilter : IPageFilter
    {
        public const string Header = "X-Test-Throw";

        public void OnPageHandlerSelected(PageHandlerSelectedContext context)
        {
        }

        public void OnPageHandlerExecuting(PageHandlerExecutingContext context)
        {
            // 再実行された /error 自体は通す（ヘッダーは再実行でも残るため）
            if (context.HttpContext.Request.Headers.ContainsKey(Header)
                && !context.HttpContext.Request.Path.Equals("/error", StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException("テスト用の想定外例外 " + TestWebApp.ThrowMarker);
            }
        }

        public void OnPageHandlerExecuted(PageHandlerExecutedContext context)
        {
        }
    }

    [Fact]
    public async Task 変更_129文字はE_PWD_LENGTH_128文字は可()
    {
        await using var app = await TestWebApp.CreateAsync();
        await app.CreateUserAsync("yamada", Initial);
        using var client = await LoginAsync(app, "yamada", Initial);

        using var tooLong = await ChangeAsync(client, Initial, new string('a', 129), new string('a', 129));
        await AssertErrorAsync(tooLong, ErrorIds.PwdLength);
        using var ok = await ChangeAsync(client, Initial, new string('a', 128), new string('a', 128));
        Assert.Equal(HttpStatusCode.Redirect, ok.StatusCode);
    }

    [Fact]
    [Trait("TC", "US-002")]
    public async Task 変更_ログイン中の利用者_他端末のセッションが失効し本人は継続()
    {
        await using var app = await TestWebApp.CreateAsync();
        var id = await app.CreateUserAsync("yamada", Initial);
        using var self = await LoginAsync(app, "yamada", Initial);
        using var otherDevice = await LoginAsync(app, "yamada", Initial);
        var stampBefore = (await ReadUserAsync(app, id)).Stamp;

        using var response = await ChangeAsync(self, Initial, "new-password-2", "new-password-2");

        Assert.Equal(HttpStatusCode.Redirect, response.StatusCode);
        Assert.NotEqual(stampBefore, (await ReadUserAsync(app, id)).Stamp);
        using var selfNext = await self.GetAsync(U("/api/probe"), Ct);
        Assert.Equal(HttpStatusCode.NotFound, selfNext.StatusCode);
        using var otherApi = await otherDevice.GetAsync(U("/api/probe"), Ct);
        Assert.Equal(HttpStatusCode.Unauthorized, otherApi.StatusCode);
        using var otherNext = await otherDevice.GetAsync(U("/error"), Ct);
        Assert.DoesNotContain("yamada さん", WebUtility.HtmlDecode(await otherNext.Content.ReadAsStringAsync(Ct)), StringComparison.Ordinal);
    }

    [Fact]
    [Trait("TC", "US-002")]
    public async Task 長さ検証_Auth_PasswordMinLengthの設定値を使う()
    {
        await using var app = await TestWebApp.CreateAsync(s => s.Configure<AuthOptions>(o => o.PasswordMinLength = 10));
        await app.CreateUserAsync("newbie", Initial, mustChangePassword: true);
        using var client = await LoginAsync(app, "newbie", Initial);

        using var nine = await ChangeAsync(client, Initial, "123456789", "123456789");
        await AssertErrorAsync(nine, ErrorIds.PwdLength);
        using var ten = await ChangeAsync(client, Initial, "1234567890", "1234567890");
        Assert.Equal(HttpStatusCode.Redirect, ten.StatusCode);
    }
}
