using System.Globalization;
using Microsoft.AspNetCore.Http;
using TodoApp.Data;

namespace TodoApp.Services;

/// <summary>一覧・カレンダー・CSV・子の遅延取得で共通の絞り込み条件（DD-06 §1）。</summary>
public sealed record TaskFilter
{
    public static readonly TaskFilter None = new();

    public long? AssigneeId { get; init; }

    /// <summary>assignee=none（担当者未設定）。</summary>
    public bool AssigneeNone { get; init; }

    public IReadOnlyList<long> StatusIds { get; init; } = [];

    public DateOnly? DueFrom { get; init; }

    public DateOnly? DueTo { get; init; }

    public bool Mine { get; init; }

    public string? Q { get; init; }

    /// <summary>（Could）memo=1: q をメモ本文にも適用（DD-06 §5）。</summary>
    public bool Memo { get; init; }

    public bool Overdue { get; init; }

    public bool Done { get; init; }

    /// <summary>sort=manual（既定は期限順）。</summary>
    public bool ManualSort { get; init; }

    /// <summary>done・sort は「条件」に含めない（DD-06 §2）。</summary>
    public bool HasConditions =>
        AssigneeId is not null || AssigneeNone || StatusIds.Count > 0 || DueFrom is not null || DueTo is not null
        || Mine || !string.IsNullOrWhiteSpace(Q) || Overdue;

    /// <summary>§1 の全条件の AND。showDone は done=1、または status に完了扱いの状態を含むとき true（DD-06 §2 手順 3）。</summary>
    /// <summary>q の正規化済み検索語。前後の空白を除いて空なら null（条件なし）。</summary>
    public string? SearchTerm => string.IsNullOrWhiteSpace(Q) ? null : TaskService.NormalizeForSearch(Q.Trim());

    public const int QMaxLength = 100;

    /// <summary>§1 のクエリの解釈。不正な値は無視して既定値にする（400 にしない。ブックマークした URL が壊れても開けるように）。</summary>
    public static TaskFilter FromQuery(IQueryCollection query)
    {
        ArgumentNullException.ThrowIfNull(query);
        var assignee = query["assignee"].ToString();
        var q = query["q"].ToString();
        return new()
        {
            AssigneeNone = assignee == "none",
            AssigneeId = ParseId(assignee),
            StatusIds = query["status"].Select(ParseId).OfType<long>().Distinct().ToList(),
            DueFrom = ParseDate(query["dueFrom"].ToString()),
            DueTo = ParseDate(query["dueTo"].ToString()),
            Mine = query["mine"] == "1",
            Q = q.Length is > 0 and <= QMaxLength ? q : null,
            Memo = query["memo"] == "1",
            Overdue = query["overdue"] == "1",
            Done = query["done"] == "1",
            ManualSort = query["sort"] == "manual",
        };
    }

    private static long? ParseId(string? value) =>
        long.TryParse(value, NumberStyles.None, CultureInfo.InvariantCulture, out var id) && id > 0 ? id : null;

    public bool Match(TaskListRecord t, bool isDone, bool showDone, long userId, DateOnly today, IReadOnlySet<long>? memoHits = null)
    {
        ArgumentNullException.ThrowIfNull(t);
        if (isDone && !showDone)
        {
            return false;
        }

        if ((AssigneeNone && t.AssigneeId is not null) || (AssigneeId is { } a && t.AssigneeId != a) || (Mine && t.AssigneeId != userId))
        {
            return false;
        }

        if (StatusIds.Count > 0 && !StatusIds.Contains(t.StatusId))
        {
            return false;
        }

        var due = ParseDate(t.DueDate);
        if ((DueFrom is not null || DueTo is not null) && (due is null || due < DueFrom || due > DueTo))
        {
            return false;
        }

        if (Overdue && (isDone || due is null || due >= today))
        {
            return false;
        }

        var q = SearchTerm;
        return q is null || t.TitleNorm.Contains(q, StringComparison.Ordinal) || (memoHits?.Contains(t.Id) ?? false);
    }

    public static DateOnly? ParseDate(string? value) =>
        value is not null && DateOnly.TryParseExact(value, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var d) ? d : null;
}
