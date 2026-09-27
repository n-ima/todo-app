using System.Data;
using TodoApp.Data;
using TodoApp.Infrastructure;

namespace TodoApp.Services;

/// <summary>状態の定義（DD-08 §1・US-015）。操作はすべて 1 トランザクションで、操作者が有効な管理者であることを先頭で確かめる。</summary>
public sealed class WorkflowService(Db db)
{
    public Task<IReadOnlyList<StatusRecord>> ListAsync(long actorId) =>
        db.WriteAsync(async (connection, transaction) =>
        {
            await UserAdminService.RequireAdminAsync(connection, transaction, actorId).ConfigureAwait(false);
            return await StatusRepository.ListAsync(connection, transaction).ConfigureAwait(false);
        });

    public Task<long> AddAsync(long actorId, string name)
    {
        var trimmed = ValidateName(name);
        return db.WriteAsync(async (connection, transaction) =>
        {
            await UserAdminService.RequireAdminAsync(connection, transaction, actorId).ConfigureAwait(false);
            await RequireUniqueAsync(connection, transaction, trimmed, 0).ConfigureAwait(false);
            return await StatusRepository.InsertAsync(connection, transaction, trimmed).ConfigureAwait(false);
        });
    }

    public Task RenameAsync(long actorId, long statusId, string name)
    {
        var trimmed = ValidateName(name);
        return db.WriteAsync(async (connection, transaction) =>
        {
            await UserAdminService.RequireAdminAsync(connection, transaction, actorId).ConfigureAwait(false);
            Find(await StatusRepository.ListAsync(connection, transaction).ConfigureAwait(false), statusId);
            await RequireUniqueAsync(connection, transaction, trimmed, statusId).ConfigureAwait(false);
            await StatusRepository.RenameAsync(connection, transaction, statusId, trimmed).ConfigureAwait(false);
            return 0;
        });
    }

    /// <summary>隣と入れ替える。端での上へ/下へは何もしない。</summary>
    public Task MoveAsync(long actorId, long statusId, bool up) =>
        db.WriteAsync(async (connection, transaction) =>
        {
            await UserAdminService.RequireAdminAsync(connection, transaction, actorId).ConfigureAwait(false);
            var list = await StatusRepository.ListAsync(connection, transaction).ConfigureAwait(false);
            var index = IndexOf(list, statusId);
            var other = up ? index - 1 : index + 1;
            if (other < 0 || other >= list.Count)
            {
                return 0;
            }

            // 削除で sort_order に欠番・重複があっても確実に入れ替わるよう、一覧上の位置で振り直す
            for (var i = 0; i < list.Count; i++)
            {
                var position = i == index ? other : i == other ? index : i;
                await StatusRepository.SetSortOrderAsync(connection, transaction, list[i].Id, position + 1).ConfigureAwait(false);
            }

            return 0;
        });

    public Task SetDoneAsync(long actorId, long statusId, bool isDone) =>
        db.WriteAsync(async (connection, transaction) =>
        {
            await UserAdminService.RequireAdminAsync(connection, transaction, actorId).ConfigureAwait(false);
            var list = await StatusRepository.ListAsync(connection, transaction).ConfigureAwait(false);
            var target = Find(list, statusId);
            if (!isDone && target.IsDone && list.Count(s => s.IsDone) == 1)
            {
                throw new AppErrorException(ErrorIds.WfNeedDone);
            }

            await StatusRepository.SetDoneAsync(connection, transaction, statusId, isDone).ConfigureAwait(false);
            return 0;
        });

    public Task DeleteAsync(long actorId, long statusId) =>
        db.WriteAsync(async (connection, transaction) =>
        {
            await UserAdminService.RequireAdminAsync(connection, transaction, actorId).ConfigureAwait(false);
            var list = await StatusRepository.ListAsync(connection, transaction).ConfigureAwait(false);
            var target = Find(list, statusId);
            if (list.Count == 1)
            {
                throw new AppErrorException(ErrorIds.WfLastStatus);
            }

            var inUse = await StatusRepository.CountTasksAsync(connection, transaction, statusId).ConfigureAwait(false);
            if (inUse > 0)
            {
                throw new AppErrorException(ErrorIds.WfInUse, inUse);
            }

            if (target.IsDone && list.Count(s => s.IsDone) == 1)
            {
                throw new AppErrorException(ErrorIds.WfNeedDone);
            }

            await StatusRepository.DeleteAsync(connection, transaction, statusId).ConfigureAwait(false);
            return 0;
        });

    private static string ValidateName(string name)
    {
        var trimmed = name.Trim();
        if (trimmed.Length is < 1 or > 20)
        {
            throw new AppErrorException(ErrorIds.Validation, "状態名（1〜20 文字）");
        }

        return trimmed;
    }

    private static async Task RequireUniqueAsync(IDbConnection connection, IDbTransaction transaction, string name, long excludeId)
    {
        if (await StatusRepository.CountByNameAsync(connection, transaction, name, excludeId).ConfigureAwait(false) > 0)
        {
            throw new AppErrorException(ErrorIds.WfNameDuplicate);
        }
    }

    private static StatusRecord Find(IReadOnlyList<StatusRecord> list, long statusId) => list[IndexOf(list, statusId)];

    private static int IndexOf(IReadOnlyList<StatusRecord> list, long statusId)
    {
        for (var i = 0; i < list.Count; i++)
        {
            if (list[i].Id == statusId)
            {
                return i;
            }
        }

        throw new AppErrorException(ErrorIds.Validation, "状態");
    }
}
