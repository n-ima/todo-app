using TodoApp.Data;
using TodoApp.Infrastructure;

namespace TodoApp.Services;

/// <summary>タスクの権限判定（DD-04 §1・requirements.md §5.0・FR-001）。画面の無効表示も同じ結果を使う。</summary>
[System.Diagnostics.CodeAnalysis.SuppressMessage("Naming", "CA1711", Justification = "設計（DD-04 §1・DD-01 §1）で定めた名前")]
public sealed class TaskPermission
{
    /// <summary>自分が担当 / 担当者未設定 / 管理者。</summary>
    public bool CanEdit(CurrentUser u, TaskRow t)
    {
        ArgumentNullException.ThrowIfNull(u);
        ArgumentNullException.ThrowIfNull(t);
        return u.IsAdmin || t.AssigneeId is null || t.AssigneeId == u.Id;
    }

    /// <summary>移動・削除: 一般利用者は配下の全タスクも編集できる必要がある（A-013）。編集できないものを返す。</summary>
    public IReadOnlyList<TaskRow> SubtreeBlockers(CurrentUser u, IEnumerable<TaskRow> subtree)
    {
        ArgumentNullException.ThrowIfNull(u);
        return u.IsAdmin ? [] : subtree.Where(t => !CanEdit(u, t)).ToList();
    }

    public void EnsureEdit(CurrentUser u, TaskRow t)
    {
        if (!CanEdit(u, t))
        {
            throw new AppErrorException(ErrorIds.PermDenied);
        }
    }

    public bool CanRestore(CurrentUser u, long deletedBy)
    {
        ArgumentNullException.ThrowIfNull(u);
        return u.IsAdmin || deletedBy == u.Id;
    }
}
