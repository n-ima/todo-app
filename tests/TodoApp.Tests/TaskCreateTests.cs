using System.Globalization;
using System.Net;
using Dapper;
using Microsoft.Data.Sqlite;
using TodoApp.Data;
using TodoApp.Infrastructure;
using TodoApp.Services;
using Xunit;

namespace TodoApp.Tests;

/// <summary>TaskPermission と入力検証の単体テスト（TASK-107・DD-04 §1・§2）。</summary>
public sealed class TaskPermissionAndValidationTests
{
    private static readonly TaskPermission Permission = new();
    private static readonly CurrentUser Member = new(10, UserAdminService.RoleMember, "一般");
    private static readonly CurrentUser Admin = new(20, UserAdminService.RoleAdmin, "管理者");

    private static TaskRow Task(long? assigneeId) => new() { Id = 1, ProjectId = 1, Title = "t", AssigneeId = assigneeId };

    [Theory]
    [Trait("TC", "FR-001")]
    // (利用者, 担当者, 期待)。一般: 自分担当/未設定は可・他人担当は不可。管理者: 常に可
    [InlineData(false, null, true)]
    [InlineData(false, 10L, true)]
    [InlineData(false, 99L, false)]
    [InlineData(true, null, true)]
    [InlineData(true, 20L, true)]
    [InlineData(true, 99L, true)]
    public void CanEdit_真理値表(bool admin, long? assigneeId, bool expected)
    {
        var user = admin ? Admin : Member;
        var task = Task(assigneeId);
        Assert.Equal(expected, Permission.CanEdit(user, task));
        if (expected)
        {
            Permission.EnsureEdit(user, task);
        }
        else
        {
            Assert.Equal(ErrorIds.PermDenied, Assert.Throws<AppErrorException>(() => Permission.EnsureEdit(user, task)).ErrorId);
        }
    }

    [Fact]
    [Trait("TC", "FR-001")]
    public void SubtreeBlockers_一般は編集できない配下を返し_管理者は空()
    {
        TaskRow[] subtree = [Task(10), Task(null), Task(99)];
        Assert.Equal([99L], Permission.SubtreeBlockers(Member, subtree).Select(t => t.AssigneeId!.Value));
        Assert.Empty(Permission.SubtreeBlockers(Admin, subtree));
    }

    [Theory]
    [Trait("TC", "US-008")]
    [InlineData(false, 10L, true)]
    [InlineData(false, 99L, false)]
    [InlineData(true, 99L, true)]
    public void CanRestore_真理値表(bool admin, long deletedBy, bool expected) =>
        Assert.Equal(expected, Permission.CanRestore(admin ? Admin : Member, deletedBy));

    private static string? ErrorOf(TaskInput input)
    {
        try
        {
            TaskService.ValidateFields(input);
            return null;
        }
        catch (AppErrorException ex)
        {
            return ex.ErrorId;
        }
    }

    [Theory]
    [Trait("TC", "US-004")]
    [InlineData(200, null)]
    [InlineData(201, "E-TASK-TITLE-LENGTH")]
    [InlineData(0, "E-TASK-TITLE-REQUIRED")]
    public void タイトル_境界値(int length, string? expected) =>
        Assert.Equal(expected, ErrorOf(new TaskInput(new string('あ', length), null, null, null)));

    [Fact]
    [Trait("TC", "US-004")]
    public void タイトル_前後の空白は除いて数え_空白だけは必須エラー_改行はE_VALIDATION()
    {
        Assert.Null(ErrorOf(new TaskInput("  " + new string('a', 200) + "  ", null, null, null)));
        Assert.Equal("E-TASK-TITLE-REQUIRED", ErrorOf(new TaskInput("   ", null, null, null)));
        Assert.Equal("E-VALIDATION", ErrorOf(new TaskInput("a\nb", null, null, null)));
        Assert.Equal("ab", TaskService.ValidateFields(new TaskInput(" ab ", null, null, null)).Title);
    }

    [Theory]
    [Trait("TC", "FR-005")]
    [InlineData(100_000, null)]
    [InlineData(100_001, "E-MEMO-TOO-LONG")]
    public void メモ_境界値(int length, string? expected) =>
        Assert.Equal(expected, ErrorOf(new TaskInput("t", null, null, null, Memo: new string('x', length))));

    [Fact]
    [Trait("TC", "FR-005")]
    public void メモ_CRLFは1文字として数える()
    {
        // 50,000 個の CRLF は正規化後 50,000 文字（上限内）。CR LF を別々に数えると 100,000 を超える
        var memo = string.Concat(Enumerable.Repeat("\r\n", 50_000)) + "x";
        Assert.Null(ErrorOf(new TaskInput("t", null, null, null, Memo: memo)));
        Assert.Equal("E-MEMO-TOO-LONG", ErrorOf(new TaskInput("t", null, null, null, Memo: memo + new string('y', 50_000))));
    }

    [Theory]
    [Trait("TC", "FR-002")]
    [InlineData("2026-10-01", "2026-10-01", null)]
    [InlineData("2026-10-01", "2026-10-02", null)]
    [InlineData("2026-10-02", "2026-10-01", "E-TASK-DATE-ORDER")]
    [InlineData("2026-02-30", null, "E-VALIDATION")]
    [InlineData(null, "2026/10/01", "E-VALIDATION")]
    [InlineData(null, null, null)]
    public void 開始日と期限(string? start, string? due, string? expected) =>
        Assert.Equal(expected, ErrorOf(new TaskInput("t", null, start, due)));

    [Theory]
    [Trait("TC", "US-004")]
    [InlineData(0, "E-VALIDATION")]
    [InlineData(1, null)]
    [InlineData(3, null)]
    [InlineData(4, "E-VALIDATION")]
    public void 優先度(int priority, string? expected) =>
        Assert.Equal(expected, ErrorOf(new TaskInput("t", null, null, null, priority)));

    [Fact]
    public void NormalizeForSearch_NFKCと小文字化() =>
        Assert.Equal("abc１".Normalize(System.Text.NormalizationForm.FormKC).ToLowerInvariant(), TaskService.NormalizeForSearch("ＡＢｃ１"));
}

/// <summary>タスク作成の結合テスト（TASK-107・US-004・DD-04 §3・DD-08 §2）。</summary>
[Collection(WebHostSerial.Name)]
public sealed class TaskCreateTests
{
    private static CancellationToken Ct => TestContext.Current.CancellationToken;

    private static string S(long id) => id.ToString(CultureInfo.InvariantCulture);

    private static SqliteConnection Open(TestWebApp app) =>
        new(new SqliteConnectionStringBuilder { DataSource = app.DatabasePath }.ToString());

    private static async Task<long> InsertProjectAsync(TestWebApp app, long userId)
    {
        await using var connection = Open(app);
        return await connection.ExecuteScalarAsync<long>(
            "INSERT INTO projects (name, sort_order, created_by, created_at, updated_at) VALUES ('案件', 1, @userId, 'x', 'x') RETURNING id",
            new { userId });
    }

    private static async Task<(TestWebApp App, HttpClient Client, long UserId, long ProjectId)> SetupAsync()
    {
        var app = await TestWebApp.CreateAsync();
        var client = await app.CreateLoggedInClientAsync("user1", "password-1");
        await using var connection = Open(app);
        var userId = await connection.ExecuteScalarAsync<long>("SELECT id FROM users WHERE login_id = 'user1'");
        return (app, client, userId, await InsertProjectAsync(app, userId));
    }

    private static Task<HttpResponseMessage> CreateUnderProjectAsync(HttpClient client, long projectId, Dictionary<string, string> fields) =>
        TestWebApp.PostFormAsync(client, $"/projects/{S(projectId)}/tasks", fields, tokenPath: $"/projects/{S(projectId)}/tasks/new");

    private static Task<HttpResponseMessage> CreateChildAsync(HttpClient client, long parentId, Dictionary<string, string> fields, long projectIdForToken) =>
        TestWebApp.PostFormAsync(client, $"/tasks/{S(parentId)}/children", fields, tokenPath: $"/projects/{S(projectIdForToken)}/tasks/new");

    private static async Task<long> CreatedIdAsync(HttpResponseMessage response)
    {
        Assert.True(response.StatusCode == HttpStatusCode.Redirect, await response.Content.ReadAsStringAsync(Ct));
        var location = response.Headers.Location!.OriginalString;
        Assert.StartsWith("/tasks/", location, StringComparison.Ordinal);
        return long.Parse(location["/tasks/".Length..], CultureInfo.InvariantCulture);
    }

    private sealed record Row(
        long project_id, long? parent_task_id, string title, string title_norm, long status_id, long? assignee_id,
        string? start_date, string? due_date, long priority, long sort_order, long created_by, long updated_by, string created_at, string updated_at);

    private static async Task<Row> RowAsync(TestWebApp app, long id)
    {
        await using var connection = Open(app);
        return await connection.QuerySingleAsync<Row>(
            "SELECT project_id, parent_task_id, title, title_norm, status_id, assignee_id, start_date, due_date, priority, sort_order, created_by, updated_by, created_at, updated_at FROM tasks WHERE id = @id",
            new { id });
    }

    private static async Task<List<(long UserId, long TaskId, string Kind, string Message)>> NotificationsAsync(TestWebApp app)
    {
        await using var connection = Open(app);
        return (await connection.QueryAsync<(long, long, string, string)>("SELECT user_id, task_id, kind, message FROM notifications ORDER BY id")).AsList();
    }

    [Fact]
    [Trait("TC", "US-004")]
    public async Task 作成_初期状態_優先度既定_sort_order_作成者を記録して詳細へ302()
    {
        var (app, client, userId, projectId) = await SetupAsync();
        await using var disposeApp = app;
        // 状態の並びを入れ替え、sort_order 最小の状態が初期値になることを確かめる
        await app.ExecuteAsync("UPDATE statuses SET sort_order = 10 WHERE name = '未着手'");
        var form = await client.GetStringAsync(new Uri($"/projects/{S(projectId)}/tasks/new", UriKind.Relative), Ct);
        Assert.Contains("タイトル（必須）", WebUtility.HtmlDecode(form), StringComparison.Ordinal);

        using var r1 = await CreateUnderProjectAsync(client, projectId, new() { ["Title"] = " ＡＢＣ 1 ", ["StartDate"] = "2026-10-01", ["DueDate"] = "2026-10-01" });
        var first = await CreatedIdAsync(r1);
        using var r2 = await CreateUnderProjectAsync(client, projectId, new() { ["Title"] = "二つ目", ["Priority"] = "1" });
        var second = await CreatedIdAsync(r2);

        var row = await RowAsync(app, first);
        await using var connection = Open(app);
        var firstStatus = await connection.ExecuteScalarAsync<long>("SELECT id FROM statuses ORDER BY sort_order LIMIT 1");
        Assert.Equal(projectId, row.project_id);
        Assert.Null(row.parent_task_id);
        Assert.Equal("ＡＢＣ 1", row.title);
        Assert.Equal("abc 1", row.title_norm);
        Assert.Equal(firstStatus, row.status_id);
        Assert.Equal(2, row.priority);
        Assert.Equal("2026-10-01", row.start_date);
        Assert.Equal("2026-10-01", row.due_date);
        Assert.Equal(1, row.sort_order);
        Assert.Equal(userId, row.created_by);
        Assert.Equal(userId, row.updated_by);
        Assert.Equal(row.created_at, row.updated_at);
        var row2 = await RowAsync(app, second);
        Assert.Equal(2, row2.sort_order);
        Assert.Equal(1, row2.priority);
        Assert.Empty(await NotificationsAsync(app));
    }

    [Fact]
    [Trait("TC", "US-004")]
    public async Task 子タスク_段数無制限_親の所属プロジェクトと兄弟ごとのsort_order()
    {
        var (app, client, _, projectId) = await SetupAsync();
        await using var disposeApp = app;
        using var r = await CreateUnderProjectAsync(client, projectId, new() { ["Title"] = "段1" });
        var parent = await CreatedIdAsync(r);
        for (var depth = 2; depth <= 8; depth++)
        {
            var form = await client.GetStringAsync(new Uri($"/tasks/{S(parent)}/children/new", UriKind.Relative), Ct);
            Assert.Contains($"action=\"/tasks/{S(parent)}/children\"", form, StringComparison.Ordinal);
            using var rc = await CreateChildAsync(client, parent, new() { ["Title"] = $"段{depth}" }, projectId);
            var child = await CreatedIdAsync(rc);
            var row = await RowAsync(app, child);
            Assert.Equal(parent, row.parent_task_id);
            Assert.Equal(projectId, row.project_id);
            Assert.Equal(1, row.sort_order);
            parent = child;
        }

        using var sibling = await CreateChildAsync(client, parent - 1, new() { ["Title"] = "兄弟" }, projectId);
        Assert.Equal(2, (await RowAsync(app, await CreatedIdAsync(sibling))).sort_order);
    }

    [Fact]
    [Trait("TC", "US-004")]
    public async Task 親がゴミ箱_または削除済みプロジェクトならE_TASK_PARENT_GONE()
    {
        var (app, client, userId, projectId) = await SetupAsync();
        await using var disposeApp = app;
        using var r = await CreateUnderProjectAsync(client, projectId, new() { ["Title"] = "親" });
        var parent = await CreatedIdAsync(r);
        await app.ExecuteAsync(
            "UPDATE tasks SET deleted_at = 'x', deleted_by = @userId, trash_root_id = id WHERE id = @parent", new { userId, parent });

        using (var rc = await CreateChildAsync(client, parent, new() { ["Title"] = "子" }, projectId))
        {
            Assert.Equal(HttpStatusCode.Conflict, rc.StatusCode);
            Assert.Contains("E-TASK-PARENT-GONE", await rc.Content.ReadAsStringAsync(Ct), StringComparison.Ordinal);
        }

        await app.ExecuteAsync("UPDATE projects SET deleted_at = 'x', deleted_by = @userId WHERE id = @projectId", new { userId, projectId });
        var other = await InsertProjectAsync(app, userId);
        using (var rp = await TestWebApp.PostFormAsync(client, $"/projects/{S(projectId)}/tasks", new Dictionary<string, string> { ["Title"] = "直下" }, tokenPath: $"/projects/{S(other)}/tasks/new"))
        {
            Assert.Equal(HttpStatusCode.Conflict, rp.StatusCode);
            Assert.Contains("E-TASK-PARENT-GONE", await rp.Content.ReadAsStringAsync(Ct), StringComparison.Ordinal);
        }

        await using var connection = Open(app);
        Assert.Equal(1, await connection.ExecuteScalarAsync<long>("SELECT COUNT(*) FROM tasks"));
    }

    [Fact]
    [Trait("TC", "US-016")]
    public async Task 割り当て通知_自分への割り当てでは作らず_他人なら作る()
    {
        var (app, client, userId, projectId) = await SetupAsync();
        await using var disposeApp = app;
        var otherId = await app.CreateUserAsync("user2", "password-2", displayName: "佐藤");
        using (var r = await CreateUnderProjectAsync(client, projectId, new() { ["Title"] = "自分用", ["AssigneeId"] = S(userId) }))
        {
            Assert.Equal(userId, (await RowAsync(app, await CreatedIdAsync(r))).assignee_id);
        }

        Assert.Empty(await NotificationsAsync(app));

        using var r2 = await CreateUnderProjectAsync(client, projectId, new() { ["Title"] = "頼む", ["AssigneeId"] = S(otherId) });
        var id = await CreatedIdAsync(r2);
        var n = Assert.Single(await NotificationsAsync(app));
        Assert.Equal((otherId, id, "assigned", "user1 さんさんが『頼む』の担当をあなたにしました"), n);
    }

    [Theory]
    [Trait("TC", "US-004")]
    [InlineData("title201", "E-TASK-TITLE-LENGTH")]
    [InlineData("dateOrder", "E-TASK-DATE-ORDER")]
    [InlineData("inactive", "E-TASK-ASSIGNEE-INACTIVE")]
    public async Task 作成_入力の誤りは画面に理由を出して作らない(string kind, string errorId)
    {
        var (app, client, _, projectId) = await SetupAsync();
        await using var disposeApp = app;
        var inactiveId = await app.CreateUserAsync("gone", "password-3", isActive: false);
        Dictionary<string, string> fields = kind switch
        {
            "title201" => new() { ["Title"] = new string('あ', 201) },
            "dateOrder" => new() { ["Title"] = "t", ["StartDate"] = "2026-10-02", ["DueDate"] = "2026-10-01" },
            _ => new() { ["Title"] = "t", ["AssigneeId"] = S(inactiveId) },
        };
        using var r = await CreateUnderProjectAsync(client, projectId, fields);
        Assert.Equal(HttpStatusCode.OK, r.StatusCode);
        Assert.Contains($"（{errorId}）", WebUtility.HtmlDecode(await r.Content.ReadAsStringAsync(Ct)), StringComparison.Ordinal);
        await using var connection = Open(app);
        Assert.Equal(0, await connection.ExecuteScalarAsync<long>("SELECT COUNT(*) FROM tasks"));
    }
}
