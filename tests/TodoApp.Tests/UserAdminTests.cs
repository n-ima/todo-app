using System.Net;
using Dapper;
using Microsoft.Data.Sqlite;
using TodoApp.Services;
using Xunit;

namespace TodoApp.Tests;

/// <summary>利用者管理の結合テスト（TASK-104・US-003・DD-03 §4）。</summary>
[Collection(WebHostSerial.Name)]
public sealed class UserAdminTests
{
    private const string UsersPath = "/admin/users";
    private const string AdminPassword = "admin-pw-1";

    private static Uri U(string path) => new(path, UriKind.Relative);

    private static CancellationToken Ct => TestContext.Current.CancellationToken;

    private static async Task<(TestWebApp App, HttpClient Admin, long AdminId)> CreateAdminAsync()
    {
        var app = await TestWebApp.CreateAsync();
        var adminId = await app.CreateUserAsync("admin1", AdminPassword, role: "admin");
        var client = app.CreateClient();
        using var response = await TestWebApp.LoginAsync(client, "admin1", AdminPassword);
        Assert.Equal(HttpStatusCode.Redirect, response.StatusCode);
        return (app, client, adminId);
    }

    private static Task<HttpResponseMessage> PostAsync(HttpClient client, string handler, Dictionary<string, string> fields) =>
        TestWebApp.PostFormAsync(client, $"{UsersPath}?handler={handler}", fields, tokenPath: UsersPath);

    private static async Task AssertErrorAsync(HttpResponseMessage response, string errorId)
    {
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Contains($"（{errorId}）", WebUtility.HtmlDecode(await response.Content.ReadAsStringAsync(Ct)), StringComparison.Ordinal);
    }

    private static async Task<T> QueryAsync<T>(TestWebApp app, string sql, object param)
    {
        await using var connection = new SqliteConnection(new SqliteConnectionStringBuilder { DataSource = app.DatabasePath }.ToString());
        return await connection.QuerySingleAsync<T>(sql, param);
    }

    [Fact]
    [Trait("TC", "US-003")]
    public async Task 登録_正常_初回ログインでパスワード変更を求められる()
    {
        var (app, admin, _) = await CreateAdminAsync();
        await using var _app = app;
        using var response = await PostAsync(admin, "Create", new() { ["loginId"] = "new.user", ["displayName"] = "新人", ["role"] = "member", ["password"] = "initial-pw-1" });
        Assert.Equal(HttpStatusCode.Redirect, response.StatusCode);

        var client = app.CreateClient();
        using var login = await TestWebApp.LoginAsync(client, "new.user", "initial-pw-1");
        Assert.Equal("/account/password", login.Headers.Location!.OriginalString);
        Assert.Equal(1L, await QueryAsync<long>(app, "SELECT must_change_password FROM users WHERE login_id = @id", new { id = "new.user" }));
    }

    [Fact]
    [Trait("TC", "US-003")]
    public async Task 登録_重複や形式違反や短いパスワード_それぞれのエラー()
    {
        var (app, admin, _) = await CreateAdminAsync();
        await using var _app = app;
        using (var r = await PostAsync(admin, "Create", new() { ["loginId"] = "ADMIN1", ["displayName"] = "x", ["role"] = "member", ["password"] = "initial-pw-1" }))
        {
            await AssertErrorAsync(r, "E-USER-DUPLICATE");
        }

        using (var r = await PostAsync(admin, "Create", new() { ["loginId"] = "bad id!", ["displayName"] = "x", ["role"] = "member", ["password"] = "initial-pw-1" }))
        {
            await AssertErrorAsync(r, "E-USER-LOGINID-FORMAT");
        }

        using (var r = await PostAsync(admin, "Create", new() { ["loginId"] = "short", ["displayName"] = "x", ["role"] = "member", ["password"] = "1234567" }))
        {
            await AssertErrorAsync(r, "E-PWD-LENGTH");
        }

        using (var r = await PostAsync(admin, "Create", new() { ["loginId"] = "role", ["displayName"] = "x", ["role"] = "boss", ["password"] = "initial-pw-1" }))
        {
            await AssertErrorAsync(r, "E-VALIDATION");
        }

        Assert.Equal(1L, await QueryAsync<long>(app, "SELECT COUNT(*) FROM users", new { }));
    }

    [Fact]
    [Trait("TC", "US-003")]
    public async Task 最後の管理者_降格と無効化は拒否_他に管理者がいれば降格できる()
    {
        var (app, admin, adminId) = await CreateAdminAsync();
        await using var _app = app;
        using (var r = await PostAsync(admin, "Update", new() { ["id"] = adminId.ToString(System.Globalization.CultureInfo.InvariantCulture), ["displayName"] = "管理", ["role"] = "member" }))
        {
            await AssertErrorAsync(r, "E-USER-LAST-ADMIN");
        }

        using (var r = await PostAsync(admin, "Deactivate", new() { ["id"] = adminId.ToString(System.Globalization.CultureInfo.InvariantCulture) }))
        {
            await AssertErrorAsync(r, "E-USER-LAST-ADMIN");
        }

        // 無効な管理者は数えない
        await app.CreateUserAsync("admin2", "admin-pw-2", role: "admin", isActive: false);
        using (var r = await PostAsync(admin, "Update", new() { ["id"] = adminId.ToString(System.Globalization.CultureInfo.InvariantCulture), ["displayName"] = "管理", ["role"] = "member" }))
        {
            await AssertErrorAsync(r, "E-USER-LAST-ADMIN");
        }

        Assert.Equal("admin", await QueryAsync<string>(app, "SELECT role FROM users WHERE id = @adminId", new { adminId }));
        await app.CreateUserAsync("admin3", "admin-pw-3", role: "admin");
        using (var r = await PostAsync(admin, "Update", new() { ["id"] = adminId.ToString(System.Globalization.CultureInfo.InvariantCulture), ["displayName"] = "管理", ["role"] = "member" }))
        {
            Assert.Equal(HttpStatusCode.Redirect, r.StatusCode);
        }

        Assert.Equal("member", await QueryAsync<string>(app, "SELECT role FROM users WHERE id = @adminId", new { adminId }));
    }

    [Fact]
    [Trait("TC", "US-003")]
    public async Task 無効化_即ログアウトされログインできない_有効化で戻る()
    {
        var (app, admin, _) = await CreateAdminAsync();
        await using var _app = app;
        var member = await app.CreateLoggedInClientAsync("member1", "member-pw-1");
        var memberId = await QueryAsync<long>(app, "SELECT id FROM users WHERE login_id = 'member1'", new { });

        using (var r = await PostAsync(admin, "Deactivate", new() { ["id"] = memberId.ToString(System.Globalization.CultureInfo.InvariantCulture) }))
        {
            Assert.Equal(HttpStatusCode.Redirect, r.StatusCode);
        }

        using (var r = await member.GetAsync(U("/account/password"), Ct))
        {
            Assert.Equal(HttpStatusCode.Redirect, r.StatusCode);
            Assert.StartsWith("/login", r.Headers.Location!.OriginalString, StringComparison.Ordinal);
        }

        using (var r = await TestWebApp.LoginAsync(app.CreateClient(), "member1", "member-pw-1"))
        {
            await AssertErrorAsync(r, "E-AUTH-FAILED");
        }

        using (var r = await PostAsync(admin, "Activate", new() { ["id"] = memberId.ToString(System.Globalization.CultureInfo.InvariantCulture) }))
        {
            Assert.Equal(HttpStatusCode.Redirect, r.StatusCode);
        }

        using (var r = await TestWebApp.LoginAsync(app.CreateClient(), "member1", "member-pw-1"))
        {
            Assert.Equal(HttpStatusCode.Redirect, r.StatusCode);
        }
    }

    [Fact]
    [Trait("TC", "US-003")]
    public async Task パスワード再設定_ロック解除され次回ログインで変更を求められる()
    {
        var (app, admin, _) = await CreateAdminAsync();
        await using var _app = app;
        var memberId = await app.CreateUserAsync("member1", "member-pw-1");
        await app.ExecuteAsync("UPDATE users SET failed_count = 3, locked_until = '2999-01-01T00:00:00.000Z' WHERE id = @memberId", new { memberId });

        using (var r = await PostAsync(admin, "ResetPassword", new() { ["id"] = memberId.ToString(System.Globalization.CultureInfo.InvariantCulture), ["password"] = "short" }))
        {
            await AssertErrorAsync(r, "E-PWD-LENGTH");
        }

        using (var r = await PostAsync(admin, "ResetPassword", new() { ["id"] = memberId.ToString(System.Globalization.CultureInfo.InvariantCulture), ["password"] = "reset-pw-1" }))
        {
            Assert.Equal(HttpStatusCode.Redirect, r.StatusCode);
        }

        using var login = await TestWebApp.LoginAsync(app.CreateClient(), "member1", "reset-pw-1");
        Assert.Equal("/account/password", login.Headers.Location!.OriginalString);
    }

    [Fact]
    [Trait("TC", "US-003")]
    public async Task 一般利用者_管理画面とPOSTは403_E_PERM_ADMIN_ONLY()
    {
        await using var app = await TestWebApp.CreateAsync();
        var adminId = await app.CreateUserAsync("admin1", AdminPassword, role: "admin");
        var member = await app.CreateLoggedInClientAsync("member1", "member-pw-1");

        using (var r = await member.GetAsync(U(UsersPath), Ct))
        {
            Assert.Equal(HttpStatusCode.Forbidden, r.StatusCode);
            Assert.Contains("（E-PERM-ADMIN-ONLY）", WebUtility.HtmlDecode(await r.Content.ReadAsStringAsync(Ct)), StringComparison.Ordinal);
        }

        using (var r = await TestWebApp.PostFormAsync(member, $"{UsersPath}?handler=Deactivate", new Dictionary<string, string> { ["id"] = adminId.ToString(System.Globalization.CultureInfo.InvariantCulture) }, tokenPath: "/account/password"))
        {
            Assert.Equal(HttpStatusCode.Forbidden, r.StatusCode);
        }

        Assert.Equal(1L, await QueryAsync<long>(app, "SELECT is_active FROM users WHERE id = @adminId", new { adminId }));
    }

    [Fact]
    [Trait("TC", "US-003")]
    public void 表示名_無効な利用者は無効の接頭辞()
    {
        Assert.Equal("（無効）山田", UserDisplay.Name("山田", isActive: false));
        Assert.Equal("山田", UserDisplay.Name("山田", isActive: true));
    }
}
