using TodoApp.Data;
using TodoApp.Infrastructure;

namespace TodoApp.Services;

/// <summary>プロジェクトの作成・名前変更・削除（DD-04 §8・US-004・US-008。管理者のみ）。</summary>
public sealed class ProjectService(Db db, IClock clock)
{
    private static readonly TimeSpan LockLifetime = TimeSpan.FromMinutes(30);

    public Task<IReadOnlyList<ProjectRecord>> ListAsync(long actorId) =>
        db.WriteAsync(async (connection, transaction) =>
        {
            await UserAdminService.RequireAdminAsync(connection, transaction, actorId).ConfigureAwait(false);
            return await ProjectRepository.ListActiveAsync(connection, transaction).ConfigureAwait(false);
        });

    public Task<long> CreateAsync(long actorId, string name)
    {
        var trimmed = ValidateName(name);
        return db.WriteAsync(async (connection, transaction) =>
        {
            await UserAdminService.RequireAdminAsync(connection, transaction, actorId).ConfigureAwait(false);
            return await ProjectRepository.InsertAsync(connection, transaction, trimmed, actorId, Now()).ConfigureAwait(false);
        });
    }

    public Task RenameAsync(long actorId, long projectId, string name)
    {
        var trimmed = ValidateName(name);
        return db.WriteAsync(async (connection, transaction) =>
        {
            await UserAdminService.RequireAdminAsync(connection, transaction, actorId).ConfigureAwait(false);
            await RequireActiveAsync(connection, transaction, projectId).ConfigureAwait(false);
            await ProjectRepository.RenameAsync(connection, transaction, projectId, trimmed, Now()).ConfigureAwait(false);
            return 0;
        });
    }

    /// <summary>projects に削除印を付けるだけで、配下のタスクの行は変えない（復元で元に戻せるように）。</summary>
    public Task DeleteAsync(long actorId, long projectId) =>
        db.WriteAsync(async (connection, transaction) =>
        {
            await UserAdminService.RequireAdminAsync(connection, transaction, actorId).ConfigureAwait(false);
            await RequireActiveAsync(connection, transaction, projectId).ConfigureAwait(false);
            var now = clock.UtcNow;
            var held = await ProjectRepository.FindOthersLockAsync(
                connection, transaction, projectId, actorId, UserRepository.FormatUtc(now - LockLifetime)).ConfigureAwait(false);
            if (held is not null)
            {
                throw new AppErrorException(ErrorIds.LockHeldSubtree, held.HolderName, held.Title);
            }

            await ProjectRepository.MarkDeletedAsync(connection, transaction, projectId, actorId, UserRepository.FormatUtc(now)).ConfigureAwait(false);
            return 0;
        });

    private static string ValidateName(string name)
    {
        var trimmed = name.Trim();
        if (trimmed.Length is < 1 or > 100)
        {
            throw new AppErrorException(ErrorIds.ProjectName);
        }

        return trimmed;
    }

    private static async Task RequireActiveAsync(System.Data.IDbConnection connection, System.Data.IDbTransaction transaction, long projectId)
    {
        if (await ProjectRepository.CountActiveAsync(connection, transaction, projectId).ConfigureAwait(false) == 0)
        {
            throw new AppErrorException(ErrorIds.Validation, "プロジェクト");
        }
    }

    private string Now() => UserRepository.FormatUtc(clock.UtcNow);
}
