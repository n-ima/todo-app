using System.Data;
using Microsoft.Extensions.Options;
using TodoApp.Cli;
using TodoApp.Data;
using TodoApp.Infrastructure;

namespace TodoApp.Services;

/// <summary>利用者管理（DD-03 §4・US-003）。どの操作も操作者が有効な管理者であることを先頭で確かめる。</summary>
public sealed class UserAdminService(Db db, IClock clock, IOptions<AuthOptions> options)
{
    public const string RoleAdmin = "admin";
    public const string RoleMember = "member";

    public Task<IReadOnlyList<UserRecord>> ListAsync(long actorId) =>
        db.WriteAsync(async (connection, transaction) =>
        {
            await RequireAdminAsync(connection, transaction, actorId).ConfigureAwait(false);
            return await UserRepository.ListAsync(connection, transaction).ConfigureAwait(false);
        });

    public Task<long> CreateAsync(long actorId, string loginId, string displayName, string role, string initialPassword)
    {
        if (!CliRunner.LoginIdPattern.IsMatch(loginId))
        {
            throw new AppErrorException(ErrorIds.UserLoginidFormat);
        }

        ValidateProfile(displayName, role);
        ValidatePassword(initialPassword);
        return db.WriteAsync(async (connection, transaction) =>
        {
            await RequireAdminAsync(connection, transaction, actorId).ConfigureAwait(false);
            if (await UserRepository.FindByLoginIdAsync(connection, transaction, loginId).ConfigureAwait(false) is not null)
            {
                throw new AppErrorException(ErrorIds.UserDuplicate);
            }

            var hash = AuthService.PasswordHasher.HashPassword(new UserRecord(), initialPassword);
            return await UserRepository.InsertAsync(connection, transaction, loginId, displayName, role, hash, Now()).ConfigureAwait(false);
        });
    }

    public Task UpdateAsync(long actorId, long targetId, string displayName, string role)
    {
        ValidateProfile(displayName, role);
        return db.WriteAsync(async (connection, transaction) =>
        {
            await RequireAdminAsync(connection, transaction, actorId).ConfigureAwait(false);
            var target = await FindTargetAsync(connection, transaction, targetId).ConfigureAwait(false);
            if (target.IsActive && target.Role == RoleAdmin && role == RoleMember)
            {
                await RequireOtherAdminAsync(connection, transaction, targetId).ConfigureAwait(false);
            }

            await UserRepository.UpdateProfileAsync(connection, transaction, targetId, displayName, role, NewStamp(), Now()).ConfigureAwait(false);
            return 0;
        });
    }

    /// <summary>無効化 / 有効化。無効化は stamp 更新で即ログアウトさせる（編集ロックの解放は LockService の実装時に接続。TASK-111）。</summary>
    public Task SetActiveAsync(long actorId, long targetId, bool isActive) =>
        db.WriteAsync(async (connection, transaction) =>
        {
            await RequireAdminAsync(connection, transaction, actorId).ConfigureAwait(false);
            var target = await FindTargetAsync(connection, transaction, targetId).ConfigureAwait(false);
            if (!isActive && target.IsActive && target.Role == RoleAdmin)
            {
                await RequireOtherAdminAsync(connection, transaction, targetId).ConfigureAwait(false);
            }

            await UserRepository.SetActiveAsync(connection, transaction, targetId, isActive, NewStamp(), Now()).ConfigureAwait(false);
            return 0;
        });

    public Task ResetPasswordAsync(long actorId, long targetId, string newPassword)
    {
        ValidatePassword(newPassword);
        return db.WriteAsync(async (connection, transaction) =>
        {
            await RequireAdminAsync(connection, transaction, actorId).ConfigureAwait(false);
            var target = await FindTargetAsync(connection, transaction, targetId).ConfigureAwait(false);
            await UserRepository.ResetPasswordAsync(
                connection, transaction, targetId, AuthService.PasswordHasher.HashPassword(target, newPassword), NewStamp(), Now()).ConfigureAwait(false);
            return 0;
        });
    }

    private static async Task RequireAdminAsync(IDbConnection connection, IDbTransaction transaction, long actorId)
    {
        var actor = await UserRepository.FindByIdAsync(connection, transaction, actorId).ConfigureAwait(false);
        if (actor is not { IsActive: true, Role: RoleAdmin })
        {
            throw new AppErrorException(ErrorIds.PermAdminOnly);
        }
    }

    private static async Task<UserRecord> FindTargetAsync(IDbConnection connection, IDbTransaction transaction, long targetId) =>
        await UserRepository.FindByIdAsync(connection, transaction, targetId).ConfigureAwait(false)
            ?? throw new AppErrorException(ErrorIds.Validation, "利用者");

    private static async Task RequireOtherAdminAsync(IDbConnection connection, IDbTransaction transaction, long targetId)
    {
        if (await UserRepository.CountOtherActiveAdminsAsync(connection, transaction, targetId).ConfigureAwait(false) == 0)
        {
            throw new AppErrorException(ErrorIds.UserLastAdmin);
        }
    }

    private static void ValidateProfile(string displayName, string role)
    {
        if (displayName.Length is < 1 or > 50)
        {
            throw new AppErrorException(ErrorIds.Validation, "表示名");
        }

        if (role is not (RoleAdmin or RoleMember))
        {
            throw new AppErrorException(ErrorIds.Validation, "ロール");
        }
    }

    private void ValidatePassword(string password)
    {
        if (password.Length < options.Value.PasswordMinLength || password.Length > 128)
        {
            throw new AppErrorException(ErrorIds.PwdLength);
        }
    }

    private string Now() => UserRepository.FormatUtc(clock.UtcNow);

    private static string NewStamp() => Guid.NewGuid().ToString("N");
}

/// <summary>担当者等の表示名。無効化された利用者は「（無効）表示名」にする（DD-03 §4。履歴はスナップショットのまま使う）。</summary>
public static class UserDisplay
{
    public static string Name(string displayName, bool isActive) => isActive ? displayName : "（無効）" + displayName;
}
