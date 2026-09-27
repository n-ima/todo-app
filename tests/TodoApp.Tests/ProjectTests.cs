using System.Globalization;
using System.Net;
using Dapper;
using Microsoft.Data.Sqlite;
using TodoApp.Data;
using Xunit;

namespace TodoApp.Tests;

/// <summary>プロジェクトの結合テスト（TASK-106・US-004・US-008・DD-04 §8）。</summary>
[Collection(WebHostSerial.Name)]
public sealed class ProjectTests
{
    private const string ProjectsPath = "/admin/projects";
    private const string AdminPassword = "admin-pw-1";
    private const string Now = "2026-09-27T00:00:00.000Z";
    private const string TaskSnapshotSql =
        "SELECT id, project_id, parent_task_id, title, status_id, sort_order, updated_at, deleted_at, deleted_by, trash_root_id FROM tasks ORDER BY id";

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
        TestWebApp.PostFormAsync(client, $"{ProjectsPath}?handler={handler}", fields, tokenPath: ProjectsPath);

    private static string S(long id) => id.ToString(CultureInfo.InvariantCulture);

    private static async Task AssertRedirectAsync(HttpResponseMessage response, string? location = null)
    {
        Assert.True(response.StatusCode == HttpStatusCode.Redirect, await response.Content.ReadAsStringAsync(Ct));
        if (location is not null)
        {
            Assert.Equal(location, response.Headers.Location?.OriginalString);
        }
    }

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

    private static SqliteConnection Open(TestWebApp app) =>
        new(new SqliteConnectionStringBuilder { DataSource = app.DatabasePath }.ToString());

    private static async Task<List<(long Id, string Name, long SortOrder, string? DeletedAt, long? DeletedBy)>> ProjectsAsync(TestWebApp app)
    {
        await using var connection = Open(app);
        return (await connection.QueryAsync<(long, string, long, string?, long?)>(
            "SELECT id, name, sort_order, deleted_at, deleted_by FROM projects ORDER BY id")).AsList();
    }

    private static async Task<List<string>> TaskSnapshotAsync(TestWebApp app)
    {
        await using var connection = Open(app);
        return (await connection.QueryAsync(TaskSnapshotSql))
            .Select(row => string.Join('|', ((IDictionary<string, object?>)row).Values))
            .ToList();
    }

    private static async Task<long> InsertTaskAsync(TestWebApp app, long projectId, long userId, string title)
    {
        await app.ExecuteAsync(
            "INSERT INTO tasks (project_id, title, title_norm, status_id, sort_order, created_by, created_at, updated_by, updated_at) " +
            "VALUES (@projectId, @title, @title, (SELECT MIN(id) FROM statuses), 1, @userId, @Now, @userId, @Now)",
            new { projectId, title, userId, Now });
        await using var connection = Open(app);
        return await connection.ExecuteScalarAsync<long>("SELECT MAX(id) FROM tasks");
    }

    private static Task InsertLockAsync(TestWebApp app, long taskId, long userId, DateTimeOffset lastActivity)
    {
        var t = UserRepository.FormatUtc(lastActivity);
        return app.ExecuteAsync(
            "INSERT INTO task_locks (task_id, user_id, acquired_at, last_activity_at) VALUES (@taskId, @userId, @t, @t)",
            new { taskId, userId, t });
    }

    [Fact]
    [Trait("TC", "US-004")]
    public async Task 作成_前後の空白を除き_重複名を許し_sort_orderは最大プラス1()
    {
        var (app, admin, adminId) = await CreateAdminAsync();
        await using var disposeApp = app;
        using (var r = await PostAsync(admin, "Create", new() { ["name"] = "  案件A  " }))
        {
            await AssertRedirectAsync(r, ProjectsPath);
        }

        using (var r = await PostAsync(admin, "Create", new() { ["name"] = "案件A" }))
        {
            await AssertRedirectAsync(r, ProjectsPath);
        }

        var projects = await ProjectsAsync(app);
        Assert.Equal(2, projects.Count);
        Assert.All(projects, p => Assert.Equal("案件A", p.Name));
        Assert.Equal([1L, 2L], projects.Select(p => p.SortOrder));
        await using var connection = Open(app);
        Assert.Equal(adminId, await connection.ExecuteScalarAsync<long>("SELECT created_by FROM projects WHERE id = @id", new { id = projects[0].Id }));

        var body = WebUtility.HtmlDecode(await admin.GetStringAsync(new Uri(ProjectsPath, UriKind.Relative), Ct));
        Assert.Contains("案件A", body, StringComparison.Ordinal);
    }

    [Fact]
    [Trait("TC", "US-004")]
    public async Task 作成_一覧のプロジェクト追加からは一覧へ戻る()
    {
        var (app, admin, _) = await CreateAdminAsync();
        await using var disposeApp = app;
        using var r = await PostAsync(admin, "Create", new() { ["name"] = "一覧から", ["returnToList"] = "true" });
        await AssertRedirectAsync(r, "/");
        Assert.Single(await ProjectsAsync(app));
    }

    [Theory]
    [Trait("TC", "US-004")]
    [InlineData(0, false)]
    [InlineData(3, true)]
    [InlineData(101, false)]
    public async Task 作成と名前変更_名前が不正ならE_PROJECT_NAME(int length, bool spacesOnly)
    {
        var value = new string(spacesOnly ? ' ' : 'あ', length);
        var (app, admin, _) = await CreateAdminAsync();
        await using var disposeApp = app;
        using (var r = await PostAsync(admin, "Create", new() { ["name"] = value }))
        {
            await AssertErrorAsync(r, "E-PROJECT-NAME", "プロジェクト名は 1〜100 文字で入力してください");
        }

        Assert.Empty(await ProjectsAsync(app));

        using (var r = await PostAsync(admin, "Create", new() { ["name"] = "元の名前" }))
        {
            await AssertRedirectAsync(r);
        }

        var id = (await ProjectsAsync(app)).Single().Id;
        using (var r = await PostAsync(admin, "Rename", new() { ["id"] = S(id), ["name"] = value }))
        {
            await AssertErrorAsync(r, "E-PROJECT-NAME");
        }

        Assert.Equal("元の名前", (await ProjectsAsync(app)).Single().Name);
    }

    [Fact]
    [Trait("TC", "US-004")]
    public async Task 名前_100文字ちょうどは作成でき_名前変更で重複名も許す()
    {
        var (app, admin, _) = await CreateAdminAsync();
        await using var disposeApp = app;
        var name100 = new string('あ', 100);
        using (var r = await PostAsync(admin, "Create", new() { ["name"] = name100 }))
        {
            await AssertRedirectAsync(r);
        }

        using (var r = await PostAsync(admin, "Create", new() { ["name"] = "既存" }))
        {
            await AssertRedirectAsync(r);
        }

        var id = (await ProjectsAsync(app))[0].Id;
        using (var r = await PostAsync(admin, "Rename", new() { ["id"] = S(id), ["name"] = " 既存 " }))
        {
            await AssertRedirectAsync(r);
        }

        Assert.All(await ProjectsAsync(app), p => Assert.Equal("既存", p.Name));
    }

    [Fact]
    [Trait("TC", "US-008")]
    public async Task 削除_削除印を付け_配下のタスクの行は変わらない()
    {
        var (app, admin, adminId) = await CreateAdminAsync();
        await using var disposeApp = app;
        using (var r = await PostAsync(admin, "Create", new() { ["name"] = "消すプロジェクト" }))
        {
            await AssertRedirectAsync(r);
        }

        var id = (await ProjectsAsync(app)).Single().Id;
        var taskId = await InsertTaskAsync(app, id, adminId, "配下1");
        await InsertTaskAsync(app, id, adminId, "配下2");
        // 自分の有効なロックは削除を妨げない
        await InsertLockAsync(app, taskId, adminId, DateTimeOffset.UtcNow);
        var before = await TaskSnapshotAsync(app);

        using (var r = await PostAsync(admin, "Delete", new() { ["id"] = S(id) }))
        {
            await AssertRedirectAsync(r);
        }

        var project = (await ProjectsAsync(app)).Single();
        Assert.NotNull(project.DeletedAt);
        Assert.Equal(adminId, project.DeletedBy);
        Assert.Equal(before, await TaskSnapshotAsync(app));
        var body = WebUtility.HtmlDecode(await admin.GetStringAsync(new Uri(ProjectsPath, UriKind.Relative), Ct));
        Assert.DoesNotContain("消すプロジェクト", body, StringComparison.Ordinal);
    }

    [Fact]
    [Trait("TC", "US-008")]
    public async Task 削除_配下に他人の有効なロックがあればE_LOCK_HELD_SUBTREE_失効したロックは妨げない()
    {
        var (app, admin, _) = await CreateAdminAsync();
        await using var disposeApp = app;
        var memberId = await app.CreateUserAsync("member1", "member-pw-1");
        using (var r = await PostAsync(admin, "Create", new() { ["name"] = "ロックあり" }))
        {
            await AssertRedirectAsync(r);
        }

        var id = (await ProjectsAsync(app)).Single().Id;
        var taskId = await InsertTaskAsync(app, id, memberId, "編集中のタスク");
        await InsertLockAsync(app, taskId, memberId, DateTimeOffset.UtcNow.AddMinutes(-29));
        string holder;
        await using (var connection = Open(app))
        {
            holder = await connection.ExecuteScalarAsync<string>("SELECT display_name FROM users WHERE id = @memberId", new { memberId }) ?? "";
        }

        var before = await TaskSnapshotAsync(app);
        using (var r = await PostAsync(admin, "Delete", new() { ["id"] = S(id) }))
        {
            await AssertErrorAsync(r, "E-LOCK-HELD-SUBTREE", $"{holder}さんが編集中のタスク『編集中のタスク』が含まれるため操作できません");
        }

        Assert.Null((await ProjectsAsync(app)).Single().DeletedAt);
        Assert.Equal(before, await TaskSnapshotAsync(app));

        await app.ExecuteAsync(
            "UPDATE task_locks SET last_activity_at = @t", new { t = UserRepository.FormatUtc(DateTimeOffset.UtcNow.AddMinutes(-31)) });
        using (var r = await PostAsync(admin, "Delete", new() { ["id"] = S(id) }))
        {
            await AssertRedirectAsync(r);
        }

        Assert.NotNull((await ProjectsAsync(app)).Single().DeletedAt);
    }

    [Fact]
    [Trait("TC", "US-004")]
    public async Task 一般利用者_画面とPOSTは403()
    {
        await using var app = await TestWebApp.CreateAsync();
        await app.CreateUserAsync("admin1", AdminPassword, role: "admin");
        var member = await app.CreateLoggedInClientAsync("member1", "member-pw-1");
        using (var r = await member.GetAsync(new Uri(ProjectsPath, UriKind.Relative), Ct))
        {
            Assert.Equal(HttpStatusCode.Forbidden, r.StatusCode);
            Assert.Contains("（E-PERM-ADMIN-ONLY）", WebUtility.HtmlDecode(await r.Content.ReadAsStringAsync(Ct)), StringComparison.Ordinal);
        }

        foreach (var handler in new[] { "Create", "Rename", "Delete" })
        {
            using var r = await TestWebApp.PostFormAsync(
                member,
                $"{ProjectsPath}?handler={handler}",
                new Dictionary<string, string> { ["name"] = "x", ["id"] = "1", ["returnToList"] = "true" },
                tokenPath: "/account/password");
            Assert.Equal(HttpStatusCode.Forbidden, r.StatusCode);
        }

        Assert.Empty(await ProjectsAsync(app));
    }
}
