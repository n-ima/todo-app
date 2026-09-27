using System.Globalization;
using System.Net;
using Dapper;
using Microsoft.Data.Sqlite;
using Xunit;

namespace TodoApp.Tests;

/// <summary>状態の定義の結合テスト（TASK-105・US-015・DD-08 §1）。</summary>
[Collection(WebHostSerial.Name)]
public sealed class WorkflowTests
{
    private const string WorkflowPath = "/admin/workflow";
    private const string AdminPassword = "admin-pw-1";

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
        TestWebApp.PostFormAsync(client, $"{WorkflowPath}?handler={handler}", fields, tokenPath: WorkflowPath);

    private static string S(long id) => id.ToString(CultureInfo.InvariantCulture);

    private static async Task AssertRedirectAsync(HttpResponseMessage response) =>
        Assert.True(response.StatusCode == HttpStatusCode.Redirect, await response.Content.ReadAsStringAsync(Ct));

    private static async Task AssertErrorAsync(HttpResponseMessage response, string errorId, string? message = null)
    {
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var body = WebUtility.HtmlDecode(await response.Content.ReadAsStringAsync(Ct));
        Assert.Contains($"（{errorId}）", body, StringComparison.Ordinal);
        if (message is not null)
        {
            Assert.Contains(message, body, StringComparison.Ordinal);
        }
    }

    private static async Task<List<(long Id, string Name, long IsDone)>> StatusesAsync(TestWebApp app)
    {
        await using var connection = new SqliteConnection(new SqliteConnectionStringBuilder { DataSource = app.DatabasePath }.ToString());
        return (await connection.QueryAsync<(long, string, long)>("SELECT id, name, is_done FROM statuses ORDER BY sort_order, id")).AsList();
    }

    private static async Task<long> IdOfAsync(TestWebApp app, string name) => (await StatusesAsync(app)).Single(s => s.Name == name).Id;

    private static async Task InsertTaskAsync(TestWebApp app, long adminId, long statusId, bool trashed)
    {
        const string now = "2026-09-27T00:00:00.000Z";
        await app.ExecuteAsync(
            "INSERT OR IGNORE INTO projects (id, name, sort_order, created_by, created_at, updated_at) VALUES (1, 'P', 1, @adminId, @now, @now)",
            new { adminId, now });
        await app.ExecuteAsync(
            "INSERT INTO tasks (project_id, title, title_norm, status_id, sort_order, created_by, created_at, updated_by, updated_at, deleted_at, deleted_by) " +
            "VALUES (1, 't', 't', @statusId, 1, @adminId, @now, @adminId, @now, @deletedAt, @deletedBy)",
            new { adminId, statusId, now, deletedAt = trashed ? now : null, deletedBy = trashed ? (long?)adminId : null });
    }

    [Fact]
    [Trait("TC", "US-015")]
    public async Task 初期状態_3つがこの順で完了だけが完了扱い()
    {
        var (app, admin, _) = await CreateAdminAsync();
        await using var _app = app;
        Assert.Equal([("未着手", 0L), ("進行中", 0L), ("完了", 1L)], (await StatusesAsync(app)).Select(s => (s.Name, s.IsDone)));

        using var r = await admin.GetAsync(new Uri(WorkflowPath, UriKind.Relative), Ct);
        Assert.Equal(HttpStatusCode.OK, r.StatusCode);
        Assert.Contains("進行中", WebUtility.HtmlDecode(await r.Content.ReadAsStringAsync(Ct)), StringComparison.Ordinal);
    }

    [Fact]
    [Trait("TC", "US-015")]
    public async Task 追加_名前変更_並べ替え_完了扱いの付け外し()
    {
        var (app, admin, _) = await CreateAdminAsync();
        await using var _app = app;
        using (var r = await PostAsync(admin, "Add", new() { ["name"] = "  レビュー中  " }))
        {
            await AssertRedirectAsync(r);
        }

        Assert.Equal(["未着手", "進行中", "完了", "レビュー中"], (await StatusesAsync(app)).Select(s => s.Name));
        Assert.Equal(0L, (await StatusesAsync(app)).Last().IsDone);

        var review = await IdOfAsync(app, "レビュー中");
        using (var r = await PostAsync(admin, "Up", new() { ["id"] = S(review) }))
        {
            await AssertRedirectAsync(r);
        }

        Assert.Equal(["未着手", "進行中", "レビュー中", "完了"], (await StatusesAsync(app)).Select(s => s.Name));

        var first = await IdOfAsync(app, "未着手");
        using (var r = await PostAsync(admin, "Down", new() { ["id"] = S(first) }))
        {
            await AssertRedirectAsync(r);
        }

        using (var r = await PostAsync(admin, "Up", new() { ["id"] = S(await IdOfAsync(app, "進行中")) }))
        {
            await AssertRedirectAsync(r);
        }

        Assert.Equal(["進行中", "未着手", "レビュー中", "完了"], (await StatusesAsync(app)).Select(s => s.Name));

        using (var r = await PostAsync(admin, "Rename", new() { ["id"] = S(review), ["name"] = "確認待ち" }))
        {
            await AssertRedirectAsync(r);
        }

        using (var r = await PostAsync(admin, "Done", new() { ["id"] = S(review), ["isDone"] = "true" }))
        {
            await AssertRedirectAsync(r);
        }

        using (var r = await PostAsync(admin, "Done", new() { ["id"] = S(await IdOfAsync(app, "完了")), ["isDone"] = "false" }))
        {
            await AssertRedirectAsync(r);
        }

        Assert.Equal([("進行中", 0L), ("未着手", 0L), ("確認待ち", 1L), ("完了", 0L)], (await StatusesAsync(app)).Select(s => (s.Name, s.IsDone)));
    }

    [Fact]
    [Trait("TC", "US-015")]
    public async Task 名前_重複と長さ違反_E_WF_NAME_DUPLICATEとE_VALIDATION()
    {
        var (app, admin, _) = await CreateAdminAsync();
        await using var _app = app;
        using (var r = await PostAsync(admin, "Add", new() { ["name"] = " 完了 " }))
        {
            await AssertErrorAsync(r, "E-WF-NAME-DUPLICATE", "同じ名前の状態が既にあります");
        }

        using (var r = await PostAsync(admin, "Rename", new() { ["id"] = S(await IdOfAsync(app, "未着手")), ["name"] = "進行中" }))
        {
            await AssertErrorAsync(r, "E-WF-NAME-DUPLICATE");
        }

        using (var r = await PostAsync(admin, "Add", new() { ["name"] = new string('あ', 21) }))
        {
            await AssertErrorAsync(r, "E-VALIDATION");
        }

        using (var r = await PostAsync(admin, "Add", new() { ["name"] = "   " }))
        {
            await AssertErrorAsync(r, "E-VALIDATION");
        }

        using (var r = await PostAsync(admin, "Add", new() { ["name"] = new string('あ', 20) }))
        {
            await AssertRedirectAsync(r);
        }

        Assert.Equal(4, (await StatusesAsync(app)).Count);
    }

    [Fact]
    [Trait("TC", "US-015")]
    public async Task 削除_使用中はゴミ箱を含めた件数でE_WF_IN_USE_未使用なら削除できる()
    {
        var (app, admin, adminId) = await CreateAdminAsync();
        await using var _app = app;
        var doing = await IdOfAsync(app, "進行中");
        await InsertTaskAsync(app, adminId, doing, trashed: false);
        await InsertTaskAsync(app, adminId, doing, trashed: true);
        using (var r = await PostAsync(admin, "Delete", new() { ["id"] = S(doing) }))
        {
            await AssertErrorAsync(r, "E-WF-IN-USE", "この状態のタスクが 2 件あります（ゴミ箱を含む）");
        }

        var todo = await IdOfAsync(app, "未着手");
        using (var r = await PostAsync(admin, "Delete", new() { ["id"] = S(todo) }))
        {
            await AssertRedirectAsync(r);
        }

        Assert.Equal(["進行中", "完了"], (await StatusesAsync(app)).Select(s => s.Name));
    }

    [Fact]
    [Trait("TC", "US-015")]
    public async Task 完了扱いが1つだけ_外すのも削除も_E_WF_NEED_DONE()
    {
        var (app, admin, _) = await CreateAdminAsync();
        await using var _app = app;
        var done = await IdOfAsync(app, "完了");
        using (var r = await PostAsync(admin, "Done", new() { ["id"] = S(done), ["isDone"] = "false" }))
        {
            await AssertErrorAsync(r, "E-WF-NEED-DONE", "完了扱いの状態が 1 つ以上必要です");
        }

        using (var r = await PostAsync(admin, "Delete", new() { ["id"] = S(done) }))
        {
            await AssertErrorAsync(r, "E-WF-NEED-DONE");
        }

        Assert.Equal(1L, (await StatusesAsync(app)).Single(s => s.Id == done).IsDone);
    }

    [Fact]
    [Trait("TC", "US-015")]
    public async Task 状態が1つだけ_削除はE_WF_LAST_STATUS()
    {
        var (app, admin, _) = await CreateAdminAsync();
        await using var _app = app;
        await app.ExecuteAsync("DELETE FROM statuses WHERE is_done = 0");
        var last = (await StatusesAsync(app)).Single().Id;
        using (var r = await PostAsync(admin, "Delete", new() { ["id"] = S(last) }))
        {
            await AssertErrorAsync(r, "E-WF-LAST-STATUS", "状態を 1 つ以上残してください");
        }

        Assert.Single(await StatusesAsync(app));
    }

    [Fact]
    [Trait("TC", "US-015")]
    public async Task 一般利用者_画面とPOSTは403()
    {
        await using var app = await TestWebApp.CreateAsync();
        await app.CreateUserAsync("admin1", AdminPassword, role: "admin");
        var member = await app.CreateLoggedInClientAsync("member1", "member-pw-1");
        using (var r = await member.GetAsync(new Uri(WorkflowPath, UriKind.Relative), Ct))
        {
            Assert.Equal(HttpStatusCode.Forbidden, r.StatusCode);
            Assert.Contains("（E-PERM-ADMIN-ONLY）", WebUtility.HtmlDecode(await r.Content.ReadAsStringAsync(Ct)), StringComparison.Ordinal);
        }

        using (var r = await TestWebApp.PostFormAsync(member, $"{WorkflowPath}?handler=Add", new Dictionary<string, string> { ["name"] = "x" }, tokenPath: "/account/password"))
        {
            Assert.Equal(HttpStatusCode.Forbidden, r.StatusCode);
        }

        Assert.Equal(3, (await StatusesAsync(app)).Count);
    }
}
