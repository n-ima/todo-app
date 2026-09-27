using TodoApp.Data;
using TodoApp.Infrastructure;

namespace TodoApp.Services;

/// <summary>一覧の 1 行（DD-06 §4）。DimNote は薄く表示する行（D）の注記。</summary>
public sealed record TaskListItem(
    long Id,
    string Title,
    int Level,
    string StatusName,
    bool StatusIsDone,
    string? AssigneeName,
    bool AssigneeInactive,
    DateOnly? DueDate,
    DueState Due,
    int Priority,
    string? DimNote,
    int ChildCount,
    bool ChildrenRendered);

public sealed record TaskListProject(long Id, string Name, IReadOnlyList<TaskListItem> Rows);

/// <summary>一覧画面の表示内容。EmptyMessage があれば表の代わりに出す。OverdueCount は絞り込みに依存しない全体の期限超過件数（DD-06 §6）。
/// Statuses・Users は絞り込みバーの選択肢。</summary>
public sealed record TaskListView(
    IReadOnlyList<TaskListProject> Projects,
    string? Notice,
    string? EmptyMessage,
    int OverdueCount,
    IReadOnlyList<StatusRecord> Statuses,
    IReadOnlyList<UserNameRecord> Users);

/// <summary>一覧の組み立てに使う読み込み結果（DD-06 §2 手順 1）。</summary>
public sealed record TaskListData(
    IReadOnlyList<ProjectRecord> Projects,
    IReadOnlyList<StatusRecord> Statuses,
    IReadOnlyList<UserNameRecord> Users,
    IReadOnlyList<TaskListRecord> Tasks,
    IReadOnlySet<long>? MemoHits = null);

/// <summary>一覧（ツリー）の組み立て（DD-06 §2〜§4・US-005・NFR-001）。</summary>
public sealed class TaskListService(Db db, IClock clock)
{
    public const int ExpandDepth = 3;
    public const int ExpandAllMax = 500;
    public const int RenderMax = 3000;

    public async Task<TaskListView> BuildAsync(TaskFilter filter, CurrentUser user) =>
        Build(await LoadAsync(filter).ConfigureAwait(false), filter, user, clock.Today).Render();

    /// <summary>折りたたまれた行の直下の子（V に含まれるもの）。有効なタスクでなければ E-TASK-NOT-FOUND。</summary>
    public async Task<IReadOnlyList<TaskListItem>> ChildrenAsync(long taskId, TaskFilter filter, CurrentUser user) =>
        Build(await LoadAsync(filter).ConfigureAwait(false), filter, user, clock.Today).Children(taskId);

    public static TaskTree Build(TaskListData data, TaskFilter filter, CurrentUser user, DateOnly today) => new(data, filter, user, today);

    private Task<TaskListData> LoadAsync(TaskFilter filter) =>
        db.WriteAsync(async (connection, transaction) => new TaskListData(
            await ProjectRepository.ListActiveAsync(connection, transaction).ConfigureAwait(false),
            await StatusRepository.ListAsync(connection, transaction).ConfigureAwait(false),
            await TaskRepository.ListUserNamesAsync(connection, transaction).ConfigureAwait(false),
            await TaskRepository.ListActiveForListAsync(connection, transaction).ConfigureAwait(false),
            filter.Memo && filter.SearchTerm is { } q
                ? (await TaskRepository.ListActiveMemosAsync(connection, transaction).ConfigureAwait(false))
                    .Where(m => TaskService.NormalizeForSearch(m.Memo).Contains(q, StringComparison.Ordinal))
                    .Select(m => m.Id)
                    .ToHashSet()
                : null));
}

/// <summary>メモリ上のツリー（一致 M・表示 V・薄い行 D = V \ M）。</summary>
public sealed class TaskTree
{
    private readonly TaskListData _data;
    private readonly bool _hasConditions;
    private readonly Dictionary<long, TaskListRecord> _byId;
    private readonly Dictionary<long, StatusRecord> _statuses;
    private readonly Dictionary<long, UserNameRecord> _users;
    private readonly HashSet<long> _matched = [];
    private readonly HashSet<long> _visible = [];
    private readonly Dictionary<long, List<TaskListRecord>> _taskChildren = [];
    private readonly Dictionary<long, List<TaskListRecord>> _projectRoots = [];
    private readonly Dictionary<long, int> _levels = [];
    private readonly DateOnly _today;
    private readonly int _overdueCount;

    internal TaskTree(TaskListData data, TaskFilter filter, CurrentUser user, DateOnly today)
    {
        _data = data;
        _today = today;
        _hasConditions = filter.HasConditions;
        _byId = data.Tasks.ToDictionary(t => t.Id);
        _statuses = data.Statuses.ToDictionary(s => s.Id);
        _users = data.Users.ToDictionary(u => u.Id);
        var showDone = filter.Done || filter.StatusIds.Any(id => _statuses.TryGetValue(id, out var s) && s.IsDone);

        foreach (var t in data.Tasks)
        {
            var isDone = _statuses[t.StatusId].IsDone;
            if (filter.Match(t, isDone, showDone, user.Id, today, data.MemoHits))
            {
                _matched.Add(t.Id);
            }

            if (DueBadge.Classify(TaskFilter.ParseDate(t.DueDate), isDone, today) == DueState.Overdue)
            {
                _overdueCount++;
            }
        }

        foreach (var id in _matched)
        {
            // 祖先をたどり、既に V にある所で止める（全体で O(n)）
            for (long? cur = id; cur is { } c && _visible.Add(c);)
            {
                cur = _byId[c].ParentTaskId;
            }
        }

        foreach (var id in _visible)
        {
            var t = _byId[id];
            var siblings = t.ParentTaskId is { } p ? GetOrAdd(_taskChildren, p) : GetOrAdd(_projectRoots, t.ProjectId);
            siblings.Add(t);
        }

        Comparison<TaskListRecord> order = filter.ManualSort ? CompareManual : CompareDue;
        foreach (var list in _taskChildren.Values.Concat(_projectRoots.Values))
        {
            list.Sort(order);
        }

        foreach (var roots in _projectRoots.Values)
        {
            SetLevels(roots, 1);
        }
    }

    public IReadOnlyCollection<long> Matched => _matched;

    public IReadOnlyCollection<long> Visible => _visible;

    public int OverdueCount => _overdueCount;

    public TaskListView Render()
    {
        if (_data.Projects.Count == 0)
        {
            return View([], null, "タスクがありません");
        }

        if (_hasConditions && _matched.Count == 0)
        {
            return View([], null, "条件に合うタスクがありません");
        }

        string? notice = null;
        var maxLevel = int.MaxValue;
        if (!_hasConditions || _visible.Count > TaskListService.ExpandAllMax)
        {
            maxLevel = TaskListService.ExpandDepth;
            notice = _hasConditions ? "該当が多いため 4 段目以降は折りたたんでいます" : null;
        }

        if (CountUpTo(maxLevel) > TaskListService.RenderMax)
        {
            maxLevel = Math.Min(maxLevel, TaskListService.ExpandDepth);
            while (maxLevel > 1 && CountUpTo(maxLevel) > TaskListService.RenderMax)
            {
                maxLevel--;
            }

            notice = $"件数が多いため {maxLevel} 段目までを表示しています。▸ で開けます";
        }

        var projects = new List<TaskListProject>();
        foreach (var p in _data.Projects)
        {
            var roots = _projectRoots.GetValueOrDefault(p.Id);
            if (_hasConditions && roots is null)
            {
                continue;
            }

            var rows = new List<TaskListItem>();
            AppendPreOrder(rows, roots ?? [], maxLevel);
            projects.Add(new(p.Id, p.Name, rows));
        }

        return View(projects, notice, null);
    }

    private TaskListView View(IReadOnlyList<TaskListProject> projects, string? notice, string? empty) =>
        new(projects, notice, empty, _overdueCount, _data.Statuses, _data.Users);

    public IReadOnlyList<TaskListItem> Children(long taskId)
    {
        if (!_byId.ContainsKey(taskId))
        {
            throw new AppErrorException(ErrorIds.TaskNotFound);
        }

        if (!_levels.TryGetValue(taskId, out var level))
        {
            return [];
        }

        return _taskChildren.GetValueOrDefault(taskId, []).Select(c => Item(c, level + 1, rendered: false)).ToList();
    }

    private void AppendPreOrder(List<TaskListItem> rows, List<TaskListRecord> siblings, int maxLevel)
    {
        foreach (var t in siblings)
        {
            var level = _levels[t.Id];
            var rendered = level < maxLevel && _taskChildren.ContainsKey(t.Id);
            rows.Add(Item(t, level, rendered));
            if (rendered)
            {
                AppendPreOrder(rows, _taskChildren[t.Id], maxLevel);
            }
        }
    }

    private TaskListItem Item(TaskListRecord t, int level, bool rendered)
    {
        var status = _statuses[t.StatusId];
        var assignee = t.AssigneeId is { } a ? _users[a] : null;
        var dimNote = _matched.Contains(t.Id) ? null : status.IsDone ? "（完了）" : "（条件外）";
        var due = TaskFilter.ParseDate(t.DueDate);
        return new(
            t.Id,
            t.Title,
            level,
            status.Name,
            status.IsDone,
            assignee?.DisplayName,
            assignee is { IsActive: false },
            due,
            DueBadge.Classify(due, status.IsDone, _today),
            t.Priority,
            dimNote,
            _taskChildren.GetValueOrDefault(t.Id)?.Count ?? 0,
            rendered);
    }

    private int CountUpTo(int maxLevel) => maxLevel == int.MaxValue ? _visible.Count : _levels.Values.Count(l => l <= maxLevel);

    private void SetLevels(List<TaskListRecord> siblings, int level)
    {
        foreach (var t in siblings)
        {
            _levels[t.Id] = level;
            if (_taskChildren.TryGetValue(t.Id, out var children))
            {
                SetLevels(children, level + 1);
            }
        }
    }

    private static List<TaskListRecord> GetOrAdd(Dictionary<long, List<TaskListRecord>> map, long key)
    {
        if (!map.TryGetValue(key, out var list))
        {
            list = [];
            map[key] = list;
        }

        return list;
    }

    // 期限順: 期限の昇順（期限なしは末尾）→ sort_order → id。yyyy-MM-dd なので序数比較で日付順になる
    private static int CompareDue(TaskListRecord x, TaskListRecord y)
    {
        var c = (x.DueDate, y.DueDate) switch
        {
            (null, null) => 0,
            (null, _) => 1,
            (_, null) => -1,
            var (a, b) => string.CompareOrdinal(a, b),
        };
        return c != 0 ? c : CompareManual(x, y);
    }

    private static int CompareManual(TaskListRecord x, TaskListRecord y)
    {
        var c = x.SortOrder.CompareTo(y.SortOrder);
        return c != 0 ? c : x.Id.CompareTo(y.Id);
    }
}
