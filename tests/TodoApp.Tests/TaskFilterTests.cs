using System.Net;
using Dapper;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.WebUtilities;
using Microsoft.Data.Sqlite;
using Microsoft.Extensions.DependencyInjection;
using TodoApp.Data;
using TodoApp.Infrastructure;
using TodoApp.Services;
using Xunit;

namespace TodoApp.Tests;

/// <summary>絞り込み・検索・期限の強調の単体テスト（TASK-109・DD-06 §1・§5・§6・US-012・US-013）。</summary>
public sealed class TaskFilterTests
{
    private static readonly CurrentUser Member = new(1, UserAdminService.RoleMember, "一般");
    private static readonly DateOnly Today = new(2026, 10, 7);

    private static TaskListRecord Task(long id, string title, long status = 1, string? due = null) => new()
    {
        Id = id, ProjectId = 1, Title = title, TitleNorm = TaskService.NormalizeForSearch(title), StatusId = status, DueDate = due, Priority = 2, SortOrder = id,
    };

    private static TaskListData Data(TaskListRecord[] tasks, IReadOnlySet<long>? memoHits = null) => new(
        [new() { Id = 1, Name = "案件", SortOrder = 1 }],
        [new() { Id = 1, Name = "未着手", SortOrder = 1 }, new() { Id = 2, Name = "完了", SortOrder = 2, IsDone = true }],
        [],
        tasks,
        memoHits);

    private static TaskTree Build(TaskFilter filter, params TaskListRecord[] tasks) => TaskListService.Build(Data(tasks), filter, Member, Today);

    private static TaskFilter Parse(string query) => TaskFilter.FromQuery(new QueryCollection(QueryHelpers.ParseQuery(query)));

    [Theory]
    [Trait("TC", "US-012")]
    [InlineData("ＡＢＣ", true)]
    [InlineData("abc", true)]
    [InlineData("  AbC  ", true)]
    [InlineData("ｱﾌﾟﾘ", true)]
    [InlineData("あぷり", false)]
    public void キーワード_全角半角と大小文字は同一視しひらがなとカタカナは区別する(string q, bool hit) =>
        Assert.Equal(hit, Build(new TaskFilter { Q = q }, Task(1, "abcのアプリ")).Matched.Contains(1));

    [Fact]
    [Trait("TC", "US-012")]
    public void キーワード_空白だけは条件なし()
    {
        var filter = new TaskFilter { Q = "   " };
        Assert.False(filter.HasConditions);
        Assert.Contains(1L, Build(filter, Task(1, "x")).Matched);
    }

    [Fact]
    [Trait("TC", "US-012")]
    public void メモ検索_メモの一致をMに加える()
    {
        var data = Data([Task(1, "タイトル"), Task(2, "別")], new HashSet<long> { 1 });
        Assert.Equal([1L], TaskListService.Build(data, new TaskFilter { Q = "本文", Memo = true }, Member, Today).Matched);
    }

    [Theory]
    [Trait("TC", "US-013")]
    [InlineData("2026-10-06", false, DueState.Overdue)]
    [InlineData("2026-10-07", false, DueState.Soon)]
    [InlineData("2026-10-08", false, DueState.Soon)]
    [InlineData("2026-10-09", false, DueState.None)]
    [InlineData(null, false, DueState.None)]
    [InlineData("2026-10-06", true, DueState.None)]
    public void 期限の区分_昨日は超過_今日明日は間近_明後日と期限なしと完了はなし(string? due, bool isDone, DueState expected) =>
        Assert.Equal(expected, DueBadge.Classify(TaskFilter.ParseDate(due), isDone, Today));

    [Fact]
    [Trait("TC", "US-013")]
    public void 期限超過件数は絞り込みに依存せず完了を除いて全体で数える()
    {
        TaskListRecord[] tasks = [Task(1, "a", due: "2026-10-01"), Task(2, "b", due: "2026-10-06"), Task(3, "c", status: 2, due: "2026-10-01"), Task(4, "d", due: "2026-10-07")];
        Assert.Equal(2, Build(TaskFilter.None, tasks).OverdueCount);
        var filtered = Build(new TaskFilter { Q = "該当なし" }, tasks);
        Assert.Empty(filtered.Matched);
        Assert.Equal(2, filtered.Render().OverdueCount);
        Assert.Equal([1L, 2L], Build(new TaskFilter { Overdue = true }, tasks).Matched.Order());
    }

    [Fact]
    [Trait("TC", "US-012")]
    public void クエリの解釈_正しい値()
    {
        var f = Parse("?assignee=5&status=1&status=2&dueFrom=2026-10-01&dueTo=2026-10-31&mine=1&q=abc&memo=1&overdue=1&done=1&sort=manual");
        Assert.Equal(5L, f.AssigneeId);
        Assert.Equal([1L, 2L], f.StatusIds);
        Assert.Equal(new DateOnly(2026, 10, 1), f.DueFrom);
        Assert.Equal(new DateOnly(2026, 10, 31), f.DueTo);
        Assert.True(f.Mine && f.Memo && f.Overdue && f.Done && f.ManualSort);
        Assert.Equal("abc", f.Q);
        Assert.True(Parse("?assignee=none").AssigneeNone);
    }

    [Fact]
    [Trait("TC", "US-012")]
    public void クエリの解釈_不正な値は無視して既定値()
    {
        var f = Parse("?assignee=abc&status=x&status=-1&dueFrom=2026/10/01&dueTo=31&mine=yes&memo=on&q=" + new string('a', 101) + "&overdue=true&done=2&sort=zzz");
        Assert.False(f.HasConditions);
        Assert.Null(f.AssigneeId);
        Assert.Empty(f.StatusIds);
        Assert.False(f.Memo || f.Done || f.ManualSort);
    }

    [Fact]
    [Trait("TC", "US-012")]
    public void 状態に完了扱いを含むときは完了も一致する() =>
        Assert.Equal([2L], Build(new TaskFilter { StatusIds = [2] }, Task(1, "a"), Task(2, "b", status: 2)).Matched);
}

/// <summary>一覧の絞り込み・期限の強調の結合テスト（TASK-109・DD-06 §1・§6・DD-11 §1）。</summary>
[Collection(WebHostSerial.Name)]
public sealed class TaskFilterWebTests
{
    private static CancellationToken Ct => TestContext.Current.CancellationToken;

    private static Uri U(string path) => new(path, UriKind.Relative);

    [Fact]
    [Trait("TC", "US-013")]
    public async Task 一覧_期限の強調は昨日今日明日明後日の境界をFakeClockで判定し_超過件数は絞り込みに依存しない()
    {
        // UTC 15:00 = 日本時間 10/7 0:00（暦日の切り替わり直後）
        var clock = new FakeClock(new DateTimeOffset(2026, 10, 6, 15, 0, 0, TimeSpan.Zero));
        await using var app = await TestWebApp.CreateAsync(s => s.AddSingleton<IClock>(clock));
        var client = await app.CreateLoggedInClientAsync();
        var userId = await app.CreateUserAsync("owner", "password-9");
        await using (var connection = new SqliteConnection(new SqliteConnectionStringBuilder { DataSource = app.DatabasePath }.ToString()))
        {
            var projectId = await connection.ExecuteScalarAsync<long>(
                "INSERT INTO projects (name, sort_order, created_by, created_at, updated_at) VALUES ('案件A', 1, @userId, 'x', 'x') RETURNING id", new { userId });
            var status = await connection.ExecuteScalarAsync<long>("SELECT id FROM statuses WHERE is_done = 0 ORDER BY sort_order LIMIT 1");
            foreach (var (title, due, memo) in new[] { ("昨日のタスク", "2026-10-06", ""), ("今日のタスク", "2026-10-07", ""), ("明日のタスク", "2026-10-08", ""), ("明後日のタスク", "2026-10-09", "メモの合言葉") })
            {
                await connection.ExecuteAsync(
                    """
                    INSERT INTO tasks (project_id, title, title_norm, status_id, due_date, memo, sort_order, created_by, created_at, updated_by, updated_at)
                    VALUES (@projectId, @title, @title, @status, @due, @memo, 1, @userId, 'x', @userId, 'x')
                    """,
                    new { projectId, title, status, due, memo, userId });
            }
        }

        var html = await client.GetStringAsync(U("/"), Ct);
        var text = WebUtility.HtmlDecode(html);
        Assert.Contains("期限超過 1 件", text, StringComparison.Ordinal);
        Assert.Contains("href=\"/?overdue=1\"", html, StringComparison.Ordinal);
        Assert.Contains("2026/10/06</span><span class=\"mark due-over\"><span aria-hidden=\"true\">!</span>期限超過", text, StringComparison.Ordinal);
        Assert.Contains("2026/10/07</span><span class=\"mark due-soon\"><span aria-hidden=\"true\">◷</span>期限間近", text, StringComparison.Ordinal);
        Assert.Contains("2026/10/08</span><span class=\"mark due-soon\">", text, StringComparison.Ordinal);
        Assert.Contains("<td class=\"c-due\">2026/10/09</td>", text, StringComparison.Ordinal);

        // 絞り込みで該当を減らしても超過件数は変わらない。ナビは絞り込みを引き継ぐ
        var filtered = WebUtility.HtmlDecode(await client.GetStringAsync(U("/?q=%E6%98%8E%E6%97%A5"), Ct));
        Assert.Contains("期限超過 1 件", filtered, StringComparison.Ordinal);
        Assert.Contains("明日のタスク", filtered, StringComparison.Ordinal);
        Assert.DoesNotContain("今日のタスク", filtered, StringComparison.Ordinal);
        Assert.Contains("href=\"/calendar?q=%E6%98%8E%E6%97%A5\"", filtered, StringComparison.Ordinal);

        var overdue = WebUtility.HtmlDecode(await client.GetStringAsync(U("/?overdue=1"), Ct));
        Assert.Contains("昨日のタスク", overdue, StringComparison.Ordinal);
        Assert.DoesNotContain("今日のタスク", overdue, StringComparison.Ordinal);

        var memoHit = WebUtility.HtmlDecode(await client.GetStringAsync(U("/?q=%E5%90%88%E8%A8%80%E8%91%89&memo=1"), Ct));
        Assert.Contains("明後日のタスク", memoHit, StringComparison.Ordinal);
        var noMemo = WebUtility.HtmlDecode(await client.GetStringAsync(U("/?q=%E5%90%88%E8%A8%80%E8%91%89"), Ct));
        Assert.Contains("条件に合うタスクがありません", noMemo, StringComparison.Ordinal);

        // 翌日に進めると今日のタスクも超過になる（絞り込み中でも全体で数える）
        // （セッションの無操作期限を越えるので別の利用者で入り直す）
        clock.UtcNow = clock.UtcNow.AddDays(1);
        var nextDay = app.CreateClient();
        using (var login = await TestWebApp.LoginAsync(nextDay, "owner", "password-9"))
        {
            Assert.Equal(HttpStatusCode.Redirect, login.StatusCode);
        }

        Assert.Contains("期限超過 2 件", WebUtility.HtmlDecode(await nextDay.GetStringAsync(U("/?mine=1"), Ct)), StringComparison.Ordinal);

        // 不正なクエリでも画面は開ける
        using var bad = await nextDay.GetAsync(U("/?assignee=x&dueFrom=bad&status=zz"), Ct);
        Assert.Equal(HttpStatusCode.OK, bad.StatusCode);
    }
}
