using System.Data;
using System.Globalization;
using System.Text;
using TodoApp.Data;
using TodoApp.Infrastructure;

namespace TodoApp.Services;

/// <summary>作成・保存で共通の入力（DD-04 §2）。StatusId・Memo は作成画面では入力しない。</summary>
public sealed record TaskInput(
    string Title,
    long? AssigneeId,
    string? StartDate,
    string? DueDate,
    int Priority = 2,
    long? StatusId = null,
    string Memo = "");

/// <summary>作成画面の表示に必要な情報。</summary>
public sealed record NewTaskContext(long ProjectId, string ProjectName, long? ParentTaskId, string? ParentTitle, IReadOnlyList<UserOption> Assignees);

/// <summary>タスクの作成（DD-04 §2・§3・US-004）。</summary>
public sealed class TaskService(Db db, IClock clock)
{
    public const int TitleMaxLength = 200;
    public const int MemoMaxLength = 100_000;

    /// <summary>検索用の正規化（DD-06 §5）。title_norm は保存のたびにこれで作り直す。</summary>
    public static string NormalizeForSearch(string value)
    {
        ArgumentNullException.ThrowIfNull(value);
        return value.Normalize(NormalizationForm.FormKC).ToLowerInvariant();
    }

    /// <summary>メモの長さ＝改行を \n に正規化したうえでの UTF-16 コード単位数（ブラウザの maxlength と同じ数え方）。</summary>
    public static string NormalizeMemo(string memo)
    {
        ArgumentNullException.ThrowIfNull(memo);
        return memo.Replace("\r\n", "\n", StringComparison.Ordinal).Replace('\r', '\n');
    }

    /// <summary>DB を見ない項目の検証（DD-04 §2）。正規化後の (タイトル, メモ) を返す。</summary>
    public static (string Title, string Memo) ValidateFields(TaskInput input)
    {
        ArgumentNullException.ThrowIfNull(input);
        var title = (input.Title ?? "").Trim();
        if (title.Length == 0)
        {
            throw new AppErrorException(ErrorIds.TaskTitleRequired);
        }

        if (title.Length > TitleMaxLength)
        {
            throw new AppErrorException(ErrorIds.TaskTitleLength);
        }

        if (title.Contains('\n', StringComparison.Ordinal) || title.Contains('\r', StringComparison.Ordinal))
        {
            throw new AppErrorException(ErrorIds.Validation, "タイトル");
        }

        var start = ParseDate(input.StartDate, "開始日");
        var due = ParseDate(input.DueDate, "期限");
        if (start is not null && due is not null && start > due)
        {
            throw new AppErrorException(ErrorIds.TaskDateOrder);
        }

        if (input.Priority is < 1 or > 3)
        {
            throw new AppErrorException(ErrorIds.Validation, "優先度");
        }

        var memo = NormalizeMemo(input.Memo ?? "");
        if (memo.Length > MemoMaxLength)
        {
            throw new AppErrorException(ErrorIds.MemoTooLong, memo.Length.ToString("N0", CultureInfo.InvariantCulture));
        }

        return (title, memo);
    }

    public Task<NewTaskContext> GetNewContextAsync(CurrentUser user, long? projectId, long? parentTaskId)
    {
        ArgumentNullException.ThrowIfNull(user);
        return db.WriteAsync(async (connection, transaction) =>
        {
            var (resolvedProjectId, projectName, parentTitle) = await ResolveParentAsync(connection, transaction, projectId, parentTaskId).ConfigureAwait(false);
            var assignees = await TaskRepository.ListActiveUsersAsync(connection, transaction).ConfigureAwait(false);
            return new NewTaskContext(resolvedProjectId, projectName, parentTaskId, parentTitle, assignees);
        });
    }

    /// <summary>プロジェクト直下（parentTaskId=null）または親タスクの下に作る。段数の上限は設けない。戻り値は tasks.id。</summary>
    public Task<long> CreateAsync(CurrentUser user, long? projectId, long? parentTaskId, TaskInput input)
    {
        ArgumentNullException.ThrowIfNull(user);
        ArgumentNullException.ThrowIfNull(input);
        return db.WriteAsync(async (connection, transaction) =>
        {
            // 作成はログイン済みなら誰でもできる（A-002）。親が有効であることだけを確かめる
            var (resolvedProjectId, _, _) = await ResolveParentAsync(connection, transaction, projectId, parentTaskId).ConfigureAwait(false);
            var (title, _) = ValidateFields(input);
            await ValidateAssigneeAsync(connection, transaction, input.AssigneeId).ConfigureAwait(false);

            var now = UserRepository.FormatUtc(clock.UtcNow);
            var statusId = await TaskRepository.FirstStatusIdAsync(connection, transaction).ConfigureAwait(false);
            var id = await TaskRepository.InsertAsync(connection, transaction, new NewTaskRecord(
                resolvedProjectId,
                parentTaskId,
                title,
                NormalizeForSearch(title),
                statusId,
                input.AssigneeId,
                Blank(input.StartDate),
                Blank(input.DueDate),
                input.Priority,
                user.Id,
                now)).ConfigureAwait(false);

            if (input.AssigneeId is { } assignee && assignee != user.Id)
            {
                var message = $"{user.DisplayName}さんが『{title}』の担当をあなたにしました";
                await TaskRepository.InsertAssignedNotificationAsync(connection, transaction, assignee, id, message, now).ConfigureAwait(false);
            }

            return id;
        });
    }

    /// <summary>親（プロジェクト、または親タスクとその所属プロジェクト）が有効か。ゴミ箱・削除済みは E-TASK-PARENT-GONE。</summary>
    private static async Task<(long ProjectId, string ProjectName, string? ParentTitle)> ResolveParentAsync(
        IDbConnection connection, IDbTransaction transaction, long? projectId, long? parentTaskId)
    {
        string? parentTitle = null;
        long resolvedProjectId;
        if (parentTaskId is { } parentId)
        {
            var parent = await TaskRepository.FindActiveAsync(connection, transaction, parentId).ConfigureAwait(false)
                ?? throw new AppErrorException(ErrorIds.TaskParentGone);
            resolvedProjectId = parent.ProjectId;
            parentTitle = parent.Title;
        }
        else
        {
            resolvedProjectId = projectId ?? throw new AppErrorException(ErrorIds.TaskParentGone);
        }

        var projectName = await TaskRepository.FindActiveProjectNameAsync(connection, transaction, resolvedProjectId).ConfigureAwait(false)
            ?? throw new AppErrorException(ErrorIds.TaskParentGone);
        return (resolvedProjectId, projectName, parentTitle);
    }

    /// <summary>作成には「現在の値」が無いので、無効な利用者は常に不可。</summary>
    private static async Task ValidateAssigneeAsync(IDbConnection connection, IDbTransaction transaction, long? assigneeId)
    {
        if (assigneeId is not { } id)
        {
            return;
        }

        var active = await TaskRepository.FindUserActiveAsync(connection, transaction, id).ConfigureAwait(false)
            ?? throw new AppErrorException(ErrorIds.Validation, "担当者");
        if (!active)
        {
            throw new AppErrorException(ErrorIds.TaskAssigneeInactive);
        }
    }

    private static DateOnly? ParseDate(string? value, string label)
    {
        if (string.IsNullOrEmpty(value))
        {
            return null;
        }

        return DateOnly.TryParseExact(value, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var date)
            ? date
            : throw new AppErrorException(ErrorIds.Validation, label);
    }

    private static string? Blank(string? value) => string.IsNullOrEmpty(value) ? null : value;
}
