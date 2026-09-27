using System.Globalization;
using System.Net;
using Dapper;
using Microsoft.Data.Sqlite;
using TodoApp.Data;
using TodoApp.Services;
using Xunit;

namespace TodoApp.Tests;

/// <summary>一覧の組み立ての単体テスト（TASK-108・DD-06 §2〜§4・US-005・NFR-001）。</summary>
public sealed class TaskListBuildTests
{
    private static readonly CurrentUser Member = new(1, UserAdminService.RoleMember, "一般");
    private static readonly DateOnly Today = new(2026, 10, 7);
    private const long Open = 1;
    private const long Done = 2;

    private sealed class Data
    {
        private long _next = 1;

        public List<ProjectRecord> Projects { get; } = [new() { Id = 1, Name = "案件", SortOrder = 1 }];

        public List<TaskListRecord> Tasks { get; } = [];

        public long Add(long? parent, string title = "t", long status = Open, string? due = null, long sortOrder = 1, long projectId = 1, long? assignee = null)
        {
            var id = _next++;
            Tasks.Add(new()
            {
                Id = id, ProjectId = projectId, ParentTaskId = parent, Title = title, TitleNorm = TaskService.NormalizeForSearch(title),
                StatusId = status, AssigneeId = assignee, DueDate = due, Priority = 2, SortOrder = sortOrder,
            });
            return id;
        }

        public TaskTree Build(TaskFilter? filter = null) => TaskListService.Build(
            new TaskListData(
                Projects,
                [new() { Id = Open, Name = "未着手", SortOrder = 1 }, new() { Id = Done, Name = "完了", SortOrder = 2, IsDone = true }],
                [new() { Id = 1, DisplayName = "一般", IsActive = true }, new() { Id = 9, DisplayName = "退職", IsActive = false }],
                Tasks),
            filter ?? TaskFilter.None,
            Member,
            Today);
    }

    private static List<TaskListItem> Rows(TaskListView view) => view.Projects.SelectMany(p => p.Rows).ToList();

    [Fact]
    [Trait("TC", "US-005")]
    public void 条件なし_3段目は見え4段目は描かれず_3段目に子の件数()
    {
        var d = new Data();
        long? parent = null;
        var ids = new List<long>();
        for (var i = 1; i <= 5; i++)
        {
            parent = d.Add(parent, $"段{i}");
            ids.Add(parent.Value);
        }

        var view = d.Build().Render();
        var rows = Rows(view);
        Assert.Equal([1, 2, 3], rows.Select(r => r.Level));
        Assert.Equal(ids.Take(3), rows.Select(r => r.Id));
        Assert.True(rows[1].ChildrenRendered);
        Assert.False(rows[2].ChildrenRendered);
        Assert.Equal(1, rows[2].ChildCount);
        Assert.Null(view.Notice);

        // 遅延取得は 4 段目を 1 件返し、それにも子があるので折りたたみ
        var child = Assert.Single(d.Build().Children(ids[2]));
        Assert.Equal((ids[3], 4, 1, false), (child.Id, child.Level, child.ChildCount, child.ChildrenRendered));
    }

    [Fact]
    [Trait("TC", "US-005")]
    public void 完了の親は未完了の子があればDとして薄く出し_未完了の子のない完了は出さない()
    {
        var d = new Data();
        var doneParent = d.Add(null, "完了の親", Done);
        var child = d.Add(doneParent, "未完了の子");
        d.Add(null, "完了だけ", Done);
        var tree = d.Build();
        Assert.Equal([child], tree.Matched);
        var rows = Rows(tree.Render());
        Assert.Equal([doneParent, child], rows.Select(r => r.Id));
        Assert.Equal("（完了）", rows[0].DimNote);
        Assert.True(rows[0].StatusIsDone);
        Assert.Null(rows[1].DimNote);

        // done=1 なら完了も一致（M）になり、薄くしない
        var all = Rows(d.Build(new TaskFilter { Done = true }).Render());
        Assert.Equal(3, all.Count);
        Assert.All(all, r => Assert.Null(r.DimNote));
    }

    [Fact]
    [Trait("TC", "US-012")]
    public void 条件あり_祖先は条件外として薄く_配下を持つプロジェクトだけ_全段を展開()
    {
        var d = new Data();
        d.Projects.Add(new() { Id = 2, Name = "空", SortOrder = 2 });
        long? parent = null;
        for (var i = 1; i <= 5; i++)
        {
            parent = d.Add(parent, $"段{i}", assignee: i == 5 ? 1 : null);
        }

        var view = d.Build(new TaskFilter { Mine = true }).Render();
        var project = Assert.Single(view.Projects);
        Assert.Equal(1, project.Id);
        Assert.Equal([1, 2, 3, 4, 5], project.Rows.Select(r => r.Level));
        Assert.Equal(["（条件外）", "（条件外）", "（条件外）", "（条件外）", null], project.Rows.Select(r => r.DimNote));

        // 条件なしなら空のプロジェクトも出す
        Assert.Equal(2, d.Build().Render().Projects.Count);
    }

    [Fact]
    [Trait("TC", "US-012")]
    public void 条件あり_Vが500行を超えると3段の規則に切り替えて注記()
    {
        var d = new Data();
        long? parent = null;
        for (var i = 0; i < 501; i++)
        {
            parent = d.Add(parent, "x");
        }

        var view = d.Build(new TaskFilter { Q = "x" }).Render();
        Assert.Equal(3, Rows(view).Count);
        Assert.Equal("該当が多いため 4 段目以降は折りたたんでいます", view.Notice);

        var d2 = new Data();
        parent = null;
        for (var i = 0; i < 500; i++)
        {
            parent = d2.Add(parent, "x");
        }

        var view2 = d2.Build(new TaskFilter { Q = "x" }).Render();
        Assert.Equal(500, Rows(view2).Count);
        Assert.Null(view2.Notice);
    }

    [Fact]
    [Trait("TC", "NFR-001")]
    public void 描画行が3000を超えると段を縮退して注記()
    {
        // 1 段目 1,000・2 段目 2,000・3 段目 2,000: 3 段で 5,000 行 > 3,000 → 2 段目まで（3,000 行）
        var d = new Data();
        for (var i = 0; i < 1000; i++)
        {
            var root = d.Add(null);
            for (var j = 0; j < 2; j++)
            {
                d.Add(d.Add(root));
            }
        }

        var view = d.Build().Render();
        var rows = Rows(view);
        Assert.Equal(3000, rows.Count);
        Assert.Equal(2, rows.Max(r => r.Level));
        Assert.All(rows.Where(r => r.Level == 2), r => Assert.False(r.ChildrenRendered));
        Assert.Equal("件数が多いため 2 段目までを表示しています。▸ で開けます", view.Notice);

        // 1 段目だけで超えるなら 1 段目までを描く
        var d2 = new Data();
        for (var i = 0; i < 3001; i++)
        {
            d2.Add(d2.Add(null));
        }

        var view2 = d2.Build().Render();
        Assert.Equal(3001, Rows(view2).Count);
        Assert.Equal("件数が多いため 1 段目までを表示しています。▸ で開けます", view2.Notice);
    }

    [Fact]
    [Trait("TC", "US-005")]
    public void 兄弟の並び_期限順は期限なしが末尾_手動順はsort_order()
    {
        var d = new Data();
        var none = d.Add(null, "期限なし", sortOrder: 1);
        var late = d.Add(null, "遅い", due: "2026-10-20", sortOrder: 2);
        var early = d.Add(null, "早い", due: "2026-10-01", sortOrder: 3);
        var tie = d.Add(null, "同日", due: "2026-10-01", sortOrder: 0);
        Assert.Equal([tie, early, late, none], Rows(d.Build().Render()).Select(r => r.Id));
        Assert.Equal([tie, none, late, early], Rows(d.Build(new TaskFilter { ManualSort = true }).Render()).Select(r => r.Id));
    }

    [Fact]
    [Trait("TC", "US-005")]
    public void 表示_担当者の無効表示と期限()
    {
        var d = new Data();
        d.Add(null, "a", due: "2026-10-09", assignee: 9);
        var row = Assert.Single(Rows(d.Build().Render()));
        Assert.Equal(("退職", true, new DateOnly(2026, 10, 9)), (row.AssigneeName, row.AssigneeInactive, row.DueDate));
    }

    [Fact]
    [Trait("TC", "US-005")]
    public void 件数0の表示()
    {
        var d = new Data();
        d.Projects.Clear();
        Assert.Equal("タスクがありません", d.Build().Render().EmptyMessage);

        var d2 = new Data();
        d2.Add(null, "abc");
        Assert.Equal("条件に合うタスクがありません", d2.Build(new TaskFilter { Q = "zzz" }).Render().EmptyMessage);
        Assert.Null(d2.Build().Render().EmptyMessage);
    }

    [Fact]
    [Trait("TC", "US-005")]
    public void 子の遅延取得_存在しないタスクはE_TASK_NOT_FOUND()
    {
        var d = new Data();
        Assert.Equal("E-TASK-NOT-FOUND", Assert.Throws<TodoApp.Infrastructure.AppErrorException>(() => d.Build().Children(99)).ErrorId);
    }
}

/// <summary>一覧画面と子の遅延取得の結合テスト（TASK-108・DD-06 §3・DD-11 §2）。</summary>
[Collection(WebHostSerial.Name)]
public sealed class TaskListWebTests
{
    private static CancellationToken Ct => TestContext.Current.CancellationToken;

    private static string S(long id) => id.ToString(CultureInfo.InvariantCulture);

    private static async Task<List<long>> InsertChainAsync(TestWebApp app, long userId, int depth)
    {
        await using var connection = new SqliteConnection(new SqliteConnectionStringBuilder { DataSource = app.DatabasePath }.ToString());
        var projectId = await connection.ExecuteScalarAsync<long>(
            "INSERT INTO projects (name, sort_order, created_by, created_at, updated_at) VALUES ('案件A', 1, @userId, 'x', 'x') RETURNING id", new { userId });
        var status = await connection.ExecuteScalarAsync<long>("SELECT id FROM statuses WHERE is_done = 0 ORDER BY sort_order LIMIT 1");
        var ids = new List<long>();
        long? parent = null;
        for (var i = 1; i <= depth; i++)
        {
            parent = await connection.ExecuteScalarAsync<long>(
                """
                INSERT INTO tasks (project_id, parent_task_id, title, title_norm, status_id, sort_order, created_by, created_at, updated_by, updated_at)
                VALUES (@projectId, @parent, @title, @title, @status, 1, @userId, 'x', @userId, 'x') RETURNING id
                """,
                new { projectId, parent, title = $"段{i}のタスク", status, userId });
            ids.Add(parent.Value);
        }

        return ids;
    }

    [Fact]
    [Trait("TC", "US-005")]
    public async Task 一覧_3段目まで描画し_4段目は子の遅延取得で返す()
    {
        await using var app = await TestWebApp.CreateAsync();
        var client = await app.CreateLoggedInClientAsync();
        var userId = await app.CreateUserAsync("owner", "password-9");
        var ids = await InsertChainAsync(app, userId, 5);

        var html = WebUtility.HtmlDecode(await client.GetStringAsync(new Uri("/", UriKind.Relative), Ct));
        Assert.Contains("案件A", html, StringComparison.Ordinal);
        Assert.Contains("段3のタスク", html, StringComparison.Ordinal);
        Assert.Contains("（子 1 件）", html, StringComparison.Ordinal);
        Assert.DoesNotContain("段4のタスク", html, StringComparison.Ordinal);
        Assert.Contains("/js/tree.js", html, StringComparison.Ordinal);
        // 一般利用者には「＋プロジェクト」を出さない
        Assert.DoesNotContain("＋プロジェクト", html, StringComparison.Ordinal);

        using var response = await client.GetAsync(new Uri($"/api/tasks/{S(ids[2])}/children", UriKind.Relative), Ct);
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var rows = WebUtility.HtmlDecode(await response.Content.ReadAsStringAsync(Ct));
        Assert.Contains($"data-id=\"{S(ids[3])}\" data-lv=\"4\"", rows, StringComparison.Ordinal);
        Assert.Contains("段4のタスク", rows, StringComparison.Ordinal);
        Assert.Contains("data-lazy=\"1\"", rows, StringComparison.Ordinal);
        Assert.DoesNotContain("段5のタスク", rows, StringComparison.Ordinal);
        Assert.DoesNotContain("<html", rows, StringComparison.Ordinal);

        using var missing = await client.GetAsync(new Uri("/api/tasks/9999/children", UriKind.Relative), Ct);
        Assert.Equal(HttpStatusCode.NotFound, missing.StatusCode);
        Assert.Contains("E-TASK-NOT-FOUND", await missing.Content.ReadAsStringAsync(Ct), StringComparison.Ordinal);

        using var anonymous = await app.CreateClient().GetAsync(new Uri($"/api/tasks/{S(ids[2])}/children", UriKind.Relative), Ct);
        Assert.Equal(HttpStatusCode.Unauthorized, anonymous.StatusCode);
    }

    [Fact]
    [Trait("TC", "US-004")]
    public async Task 一覧_0件の表示と管理者のプロジェクト作成で一覧へ戻る()
    {
        await using var app = await TestWebApp.CreateAsync();
        await app.CreateUserAsync("admin1", "password-a", role: "admin");
        var client = app.CreateClient();
        using (var login = await TestWebApp.LoginAsync(client, "admin1", "password-a"))
        {
            Assert.Equal(HttpStatusCode.Redirect, login.StatusCode);
        }

        var html = WebUtility.HtmlDecode(await client.GetStringAsync(new Uri("/", UriKind.Relative), Ct));
        Assert.Contains("タスクがありません", html, StringComparison.Ordinal);
        Assert.Contains("＋プロジェクト", html, StringComparison.Ordinal);
        Assert.Contains("action=\"/admin/projects?handler=Create\"", html, StringComparison.Ordinal);

        using var created = await TestWebApp.PostFormAsync(
            client, "/admin/projects?handler=Create", new Dictionary<string, string> { ["name"] = "新案件", ["returnToList"] = "true" }, tokenPath: "/");
        Assert.Equal(HttpStatusCode.Redirect, created.StatusCode);
        Assert.Equal("/", created.Headers.Location!.OriginalString);
        var after = WebUtility.HtmlDecode(await client.GetStringAsync(new Uri("/", UriKind.Relative), Ct));
        Assert.Contains("新案件", after, StringComparison.Ordinal);
        Assert.DoesNotContain("タスクがありません", after, StringComparison.Ordinal);
    }
}
