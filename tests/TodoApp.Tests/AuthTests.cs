using System.Net;
using System.Text.Json;
using Dapper;
using Microsoft.AspNetCore.Identity;
using Microsoft.Data.Sqlite;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Options;
using TodoApp.Data;
using TodoApp.Infrastructure;
using Xunit;

namespace TodoApp.Tests;

/// <summary>ログイン・Cookie 認証・Antiforgery の結合テスト（TASK-101・US-001・DD-03 §1・§2・DD-01 §6）。</summary>
[Collection(WebHostSerial.Name)]
public sealed class AuthTests
{
    private const string Password = "correct-horse-1";
    private static readonly Uri ApiProbe = new("/api/probe", UriKind.Relative);

    private static Uri U(string path) => new(path, UriKind.Relative);

    private static CancellationToken Ct => TestContext.Current.CancellationToken;

    private static Task<TestWebApp> CreateAppAsync(FakeClock clock) =>
        TestWebApp.CreateAsync(s => s.AddSingleton<IClock>(clock));

    private static FakeClock NewClock() => new(new DateTimeOffset(2026, 9, 28, 0, 0, 0, TimeSpan.Zero));

    private static async Task<string?> ApiErrorIdAsync(HttpResponseMessage response)
    {
        using var json = JsonDocument.Parse(await response.Content.ReadAsStringAsync(Ct));
        return json.RootElement.GetProperty("error").GetProperty("id").GetString();
    }

    private static async Task AssertLoginErrorAsync(HttpResponseMessage response, string errorId)
    {
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Contains($"（{errorId}）", await response.Content.ReadAsStringAsync(Ct), StringComparison.Ordinal);
    }

    private static async Task<(long FailedCount, string? LockedUntil)> ReadLockoutAsync(TestWebApp app, long id)
    {
        await using var connection = new SqliteConnection(new SqliteConnectionStringBuilder { DataSource = app.DatabasePath }.ToString());
        return await connection.QuerySingleAsync<(long, string?)>("SELECT failed_count, locked_until FROM users WHERE id = @id", new { id });
    }

    [Fact]
    [Trait("TC", "US-001")]
    public async Task ログイン_正しいIDとパスワード_一覧へ転送しセッションCookieを発行()
    {
        await using var app = await TestWebApp.CreateAsync();
        await app.CreateUserAsync("yamada", Password);
        using var client = app.CreateClient();

        using var response = await TestWebApp.LoginAsync(client, "YAMADA", Password);

        Assert.Equal(HttpStatusCode.Redirect, response.StatusCode);
        Assert.Equal("/", response.Headers.Location?.OriginalString);
        var cookie = Assert.Single(response.Headers.GetValues("Set-Cookie"), c => c.StartsWith(AuthSetup.CookieName + "=", StringComparison.Ordinal));
        Assert.Contains("httponly", cookie, StringComparison.OrdinalIgnoreCase);
        Assert.Contains("secure", cookie, StringComparison.OrdinalIgnoreCase);
        Assert.Contains("samesite=strict", cookie, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("expires", cookie, StringComparison.OrdinalIgnoreCase);
        using var api = await client.GetAsync(ApiProbe, Ct);
        Assert.Equal(HttpStatusCode.NotFound, api.StatusCode);
    }

    [Fact]
    public async Task ログイン_管理者_ロールと表示名がクレームで効く()
    {
        await using var app = await TestWebApp.CreateAsync();
        await app.CreateUserAsync("admin1", Password, role: "admin", displayName: "管理 太郎");
        using var client = app.CreateClient();
        using (var login = await TestWebApp.LoginAsync(client, "admin1", Password))
        {
            Assert.Equal(HttpStatusCode.Redirect, login.StatusCode);
        }

        using var response = await client.GetAsync(U("/error"), Ct);
        var body = WebUtility.HtmlDecode(await response.Content.ReadAsStringAsync(Ct));

        Assert.Contains("<a href=\"/admin/users\"", body, StringComparison.Ordinal);
        Assert.Contains("<summary>管理 太郎</summary>", body, StringComparison.Ordinal);
    }

    [Fact]
    public async Task ログイン_初期パスワードのまま_パスワード変更へ転送()
    {
        await using var app = await TestWebApp.CreateAsync();
        await app.CreateUserAsync("newbie", Password, mustChangePassword: true);
        using var client = app.CreateClient();

        using var response = await TestWebApp.LoginAsync(client, "newbie", Password, returnUrl: "/tasks/1");

        Assert.Equal(HttpStatusCode.Redirect, response.StatusCode);
        Assert.Equal("/account/password", response.Headers.Location?.OriginalString);
    }

    [Theory]
    [Trait("TC", "US-001")]
    [InlineData("yamada", "wrong-password")]
    [InlineData("nobody", Password)]
    [InlineData("", Password)]
    public async Task ログイン_誤りまたは存在しないID_区別しないE_AUTH_FAILED(string loginId, string password)
    {
        await using var app = await TestWebApp.CreateAsync();
        await app.CreateUserAsync("yamada", Password);
        using var client = app.CreateClient();

        using var response = await TestWebApp.LoginAsync(client, loginId, password);

        await AssertLoginErrorAsync(response, ErrorIds.AuthFailed);
        Assert.DoesNotContain(response.Headers.TryGetValues("Set-Cookie", out var cookies) ? cookies : [], c => c.StartsWith(AuthSetup.CookieName + "=", StringComparison.Ordinal));
    }

    [Fact]
    [Trait("TC", "US-001")]
    public async Task ログイン_無効化された利用者_正しいパスワードでもE_AUTH_FAILED()
    {
        await using var app = await TestWebApp.CreateAsync();
        await app.CreateUserAsync("gone", Password, isActive: false);
        using var client = app.CreateClient();

        using var response = await TestWebApp.LoginAsync(client, "gone", Password);

        await AssertLoginErrorAsync(response, ErrorIds.AuthFailed);
    }

    [Fact]
    [Trait("TC", "US-001")]
    public async Task ログイン_5回失敗_5回目でロックし6回目は正しくてもE_AUTH_LOCKED_15分後に解除()
    {
        var clock = NewClock();
        await using var app = await CreateAppAsync(clock);
        var id = await app.CreateUserAsync("yamada", Password);
        using var client = app.CreateClient();

        for (var i = 1; i <= 4; i++)
        {
            using var failed = await TestWebApp.LoginAsync(client, "yamada", "wrong");
            await AssertLoginErrorAsync(failed, ErrorIds.AuthFailed);
        }

        Assert.Equal((4L, (string?)null), await ReadLockoutAsync(app, id));
        using (var fifth = await TestWebApp.LoginAsync(client, "yamada", "wrong"))
        {
            await AssertLoginErrorAsync(fifth, ErrorIds.AuthFailed);
        }

        Assert.Equal((0L, "2026-09-28T00:15:00.000Z"), await ReadLockoutAsync(app, id));
        using (var sixth = await TestWebApp.LoginAsync(client, "yamada", Password))
        {
            await AssertLoginErrorAsync(sixth, ErrorIds.AuthLocked);
        }

        clock.UtcNow = clock.UtcNow.AddMinutes(15).AddMilliseconds(-1);
        using (var stillLocked = await TestWebApp.LoginAsync(client, "yamada", Password))
        {
            await AssertLoginErrorAsync(stillLocked, ErrorIds.AuthLocked);
        }

        clock.UtcNow = clock.UtcNow.AddMilliseconds(1);
        using (var unlocked = await TestWebApp.LoginAsync(client, "yamada", Password))
        {
            Assert.Equal(HttpStatusCode.Redirect, unlocked.StatusCode);
        }

        Assert.Equal((0L, (string?)null), await ReadLockoutAsync(app, id));
    }

    [Fact]
    public async Task ログイン_成功で失敗回数を0に戻す()
    {
        await using var app = await TestWebApp.CreateAsync();
        var id = await app.CreateUserAsync("yamada", Password);
        using var client = app.CreateClient();
        using (var failed = await TestWebApp.LoginAsync(client, "yamada", "wrong"))
        {
            await AssertLoginErrorAsync(failed, ErrorIds.AuthFailed);
        }

        using var ok = await TestWebApp.LoginAsync(client, "yamada", Password);

        Assert.Equal(HttpStatusCode.Redirect, ok.StatusCode);
        Assert.Equal((0L, (string?)null), await ReadLockoutAsync(app, id));
    }

    [Fact]
    public async Task ログイン_旧形式のハッシュ_成功時に再ハッシュする()
    {
        await using var app = await TestWebApp.CreateAsync();
        var id = await app.CreateUserAsync("old", Password);
        var v2 = new PasswordHasher<UserRecord>(Options.Create(new PasswordHasherOptions { CompatibilityMode = PasswordHasherCompatibilityMode.IdentityV2 }))
            .HashPassword(new UserRecord(), Password);
        await app.ExecuteAsync("UPDATE users SET password_hash = @v2 WHERE id = @id", new { v2, id });
        using var client = app.CreateClient();

        using var response = await TestWebApp.LoginAsync(client, "old", Password);

        Assert.Equal(HttpStatusCode.Redirect, response.StatusCode);
        await using var connection = new SqliteConnection(new SqliteConnectionStringBuilder { DataSource = app.DatabasePath }.ToString());
        var stored = await connection.ExecuteScalarAsync<string>("SELECT password_hash FROM users WHERE id = @id", new { id });
        Assert.NotEqual(v2, stored);
        Assert.Equal(PasswordVerificationResult.Success, new PasswordHasher<UserRecord>().VerifyHashedPassword(new UserRecord(), stored!, Password));
    }

    [Theory]
    [Trait("TC", "US-001")]
    [InlineData("https://evil.example/", "/")]
    [InlineData("//evil.example/x", "/")]
    [InlineData("/\\evil.example", "/")]
    [InlineData("/tasks/5?view=1", "/tasks/5?view=1")]
    public async Task ログイン_returnUrl_ローカルだけ使い外部は無視(string returnUrl, string expected)
    {
        await using var app = await TestWebApp.CreateAsync();
        await app.CreateUserAsync("yamada", Password);
        using var client = app.CreateClient();

        using var response = await TestWebApp.LoginAsync(client, "yamada", Password, returnUrl);

        Assert.Equal(HttpStatusCode.Redirect, response.StatusCode);
        Assert.Equal(expected, response.Headers.Location?.OriginalString);
    }

    [Fact]
    [Trait("TC", "US-001")]
    public async Task 未ログイン_画面はreturnUrl付きでログインへ_APIは401()
    {
        await using var app = await TestWebApp.CreateAsync();
        using var client = app.CreateClient();

        using var page = await client.GetAsync(U("/logout?x=1"), Ct);
        using var api = await client.GetAsync(ApiProbe, Ct);
        using var login = await client.GetAsync(U("/login"), Ct);

        Assert.Equal(HttpStatusCode.Redirect, page.StatusCode);
        Assert.Equal("/login?returnUrl=%2Flogout%3Fx%3D1", page.Headers.Location?.OriginalString);
        Assert.Equal(HttpStatusCode.Unauthorized, api.StatusCode);
        Assert.Equal(ErrorIds.AuthRequired, await ApiErrorIdAsync(api));
        Assert.Equal(HttpStatusCode.OK, login.StatusCode);
    }

    [Fact]
    [Trait("TC", "US-001")]
    public async Task セッション_操作のたびに延長し_最後の操作から8時間で失効()
    {
        var clock = NewClock();
        await using var app = await CreateAppAsync(clock);
        await app.CreateUserAsync("yamada", Password);
        using var client = app.CreateClient();
        using (var login = await TestWebApp.LoginAsync(client, "yamada", Password))
        {
            Assert.Equal(HttpStatusCode.Redirect, login.StatusCode);
        }

        // 半分（4 時間）を過ぎない間隔でも毎回延長されること（標準のスライディングでは 8 時間で切れる）
        for (var i = 0; i < 3; i++)
        {
            clock.UtcNow = clock.UtcNow.AddHours(3);
            using var alive = await client.GetAsync(ApiProbe, Ct);
            Assert.Equal(HttpStatusCode.NotFound, alive.StatusCode);
        }

        clock.UtcNow = clock.UtcNow.AddHours(7).AddMinutes(59);
        using (var justBefore = await client.GetAsync(ApiProbe, Ct))
        {
            Assert.Equal(HttpStatusCode.NotFound, justBefore.StatusCode);
        }

        clock.UtcNow = clock.UtcNow.AddHours(8).AddSeconds(1);
        using var expiredApi = await client.GetAsync(ApiProbe, Ct);
        using var expiredPage = await client.GetAsync(U("/logout"), Ct);

        Assert.Equal(HttpStatusCode.Unauthorized, expiredApi.StatusCode);
        Assert.Equal(ErrorIds.AuthRequired, await ApiErrorIdAsync(expiredApi));
        Assert.Equal(HttpStatusCode.Redirect, expiredPage.StatusCode);
        Assert.StartsWith("/login?returnUrl=", expiredPage.Headers.Location?.OriginalString, StringComparison.Ordinal);
    }

    [Fact]
    public async Task セッション_healthzは操作に数えず延長しない()
    {
        var clock = NewClock();
        await using var app = await CreateAppAsync(clock);
        await app.CreateUserAsync("yamada", Password);
        using var client = app.CreateClient();
        using (var login = await TestWebApp.LoginAsync(client, "yamada", Password))
        {
            Assert.Equal(HttpStatusCode.Redirect, login.StatusCode);
        }

        clock.UtcNow = clock.UtcNow.AddHours(5);
        using (var health = await client.GetAsync(U("/healthz"), Ct))
        {
            Assert.Equal(HttpStatusCode.OK, health.StatusCode);
            Assert.False(health.Headers.Contains("Set-Cookie"));
        }

        clock.UtcNow = clock.UtcNow.AddHours(3).AddSeconds(1);
        using var expired = await client.GetAsync(ApiProbe, Ct);
        Assert.Equal(HttpStatusCode.Unauthorized, expired.StatusCode);
    }

    [Theory]
    [Trait("TC", "US-001")]
    [InlineData("UPDATE users SET is_active = 0 WHERE login_id = 'yamada'")]
    [InlineData("UPDATE users SET security_stamp = 'changed' WHERE login_id = 'yamada'")]
    public async Task セッション_無効化やstamp更新_次の要求で即時失効(string sql)
    {
        await using var app = await TestWebApp.CreateAsync();
        using var client = await app.CreateLoggedInClientAsync("yamada", Password);
        using (var before = await client.GetAsync(ApiProbe, Ct))
        {
            Assert.Equal(HttpStatusCode.NotFound, before.StatusCode);
        }

        await app.ExecuteAsync(sql);
        using var after = await client.GetAsync(ApiProbe, Ct);

        Assert.Equal(HttpStatusCode.Unauthorized, after.StatusCode);
        Assert.Equal(ErrorIds.AuthRequired, await ApiErrorIdAsync(after));
    }

    [Fact]
    public async Task セッション_表示名の変更はDBの値でクレームを差し替える()
    {
        await using var app = await TestWebApp.CreateAsync();
        using var client = await app.CreateLoggedInClientAsync("yamada", Password);

        await app.ExecuteAsync("UPDATE users SET display_name = '新しい名前' WHERE login_id = 'yamada'");
        using var response = await client.GetAsync(U("/error"), Ct);
        var body = WebUtility.HtmlDecode(await response.Content.ReadAsStringAsync(Ct));

        Assert.Contains("<summary>新しい名前</summary>", body, StringComparison.Ordinal);
    }

    [Fact]
    public async Task ログアウト_ログインへ転送しセッションを終える()
    {
        await using var app = await TestWebApp.CreateAsync();
        using var client = await app.CreateLoggedInClientAsync("yamada", Password);

        using var response = await TestWebApp.PostFormAsync(client, "/logout", new Dictionary<string, string>());
        using var api = await client.GetAsync(ApiProbe, Ct);

        Assert.Equal(HttpStatusCode.Redirect, response.StatusCode);
        Assert.Equal("/login", response.Headers.Location?.OriginalString);
        Assert.Equal(HttpStatusCode.Unauthorized, api.StatusCode);
    }

    [Theory]
    [Trait("TC", "US-001")]
    [InlineData("/login", false)]
    [InlineData("/logout", true)]
    public async Task CSRF_トークン無しのフォームPOST_400のE_CSRF画面(string path, bool loggedIn)
    {
        await using var app = await TestWebApp.CreateAsync();
        using var client = loggedIn ? await app.CreateLoggedInClientAsync("yamada", Password) : app.CreateClient();
        await app.CreateUserAsync("other", Password);
        using var content = new FormUrlEncodedContent(new Dictionary<string, string> { ["loginId"] = "other", ["password"] = Password });

        using var response = await client.PostAsync(U(path), content, Ct);
        var body = await response.Content.ReadAsStringAsync(Ct);

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        Assert.Equal("text/html", response.Content.Headers.ContentType?.MediaType);
        Assert.Contains($"（{ErrorIds.Csrf}）", body, StringComparison.Ordinal);
        Assert.Contains("<meta name=\"csrf-token\"", body, StringComparison.Ordinal);
        Assert.False(response.Headers.Location is not null);
        if (loggedIn)
        {
            // ログアウトされていない
            using var api = await client.GetAsync(ApiProbe, Ct);
            Assert.Equal(HttpStatusCode.NotFound, api.StatusCode);
        }
    }

    [Fact]
    [Trait("TC", "US-001")]
    public async Task CSRF_APIの状態変更_ヘッダー無しは400のJSON_ヘッダー有りは通す()
    {
        await using var app = await TestWebApp.CreateAsync();
        using var client = await app.CreateLoggedInClientAsync("yamada", Password);

        using var noToken = await client.PostAsync(ApiProbe, new StringContent("{}"), Ct);
        Assert.Equal(HttpStatusCode.BadRequest, noToken.StatusCode);
        Assert.Equal(ErrorIds.Csrf, await ApiErrorIdAsync(noToken));

        var token = await TestWebApp.GetTokenAsync(client);
        using var request = new HttpRequestMessage(HttpMethod.Delete, ApiProbe);
        request.Headers.Add("RequestVerificationToken", token);
        using var withToken = await client.SendAsync(request, Ct);
        Assert.Equal(HttpStatusCode.NotFound, withToken.StatusCode);
    }

    [Fact]
    [Trait("TC", "US-001")]
    public async Task ログイン_空入力_英語の必須エラーを出さず日本語のE_AUTH_FAILEDだけ()
    {
        await using var app = await TestWebApp.CreateAsync();
        using var client = app.CreateClient();

        using var response = await TestWebApp.LoginAsync(client, "", "");

        await AssertLoginErrorAsync(response, ErrorIds.AuthFailed);
        var body = WebUtility.HtmlDecode(await response.Content.ReadAsStringAsync(Ct));
        Assert.Contains(ErrorIds.Messages[ErrorIds.AuthFailed], body, StringComparison.Ordinal);
        // レイアウトの ModelState 要約が出ると 2 つ目の error-summary になる
        Assert.Single(System.Text.RegularExpressions.Regex.Matches(body, "error-summary"));
        Assert.DoesNotContain("field is required", body, StringComparison.Ordinal);
    }
}
