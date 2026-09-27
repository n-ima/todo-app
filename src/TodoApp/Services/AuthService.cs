using Microsoft.AspNetCore.Identity;
using Microsoft.Extensions.Options;
using TodoApp.Data;
using TodoApp.Infrastructure;

namespace TodoApp.Services;

/// <summary>ログインの結果。失敗の書き込み（失敗回数・ロック）をコミットするため例外ではなく結果で返す。</summary>
public sealed record LoginResult(UserRecord? User, string? ErrorId)
{
    public bool Succeeded => User is not null;
}

/// <summary>ログイン（DD-03 §2・NFR-005）。</summary>
public sealed partial class AuthService(Db db, IClock clock, IOptions<AuthOptions> options, ILogger<AuthService> logger)
{
    private static readonly PasswordHasher<UserRecord> Hasher = new();

    // 存在しない ID でも検証 1 回分の時間をかけるためのハッシュ（値に意味は無い）
    private static readonly Lazy<string> DummyHash = new(() => Hasher.HashPassword(new UserRecord(), Guid.NewGuid().ToString("N")));

    public static PasswordHasher<UserRecord> PasswordHasher => Hasher;

    [LoggerMessage(Level = LogLevel.Warning, Message = "ログイン失敗 {ErrorId} ログイン ID: {LoginId} 送信元: {RemoteIp}")]
    private static partial void LogLoginFailed(ILogger logger, string errorId, string loginId, string remoteIp);

    public async Task<LoginResult> LoginAsync(string loginId, string password, string remoteIp)
    {
        var result = await db.WriteAsync(async (connection, transaction) =>
        {
            var now = clock.UtcNow;
            var nowText = UserRepository.FormatUtc(now);
            var user = await UserRepository.FindByLoginIdAsync(connection, transaction, loginId).ConfigureAwait(false);
            if (user is null)
            {
                Hasher.VerifyHashedPassword(new UserRecord(), DummyHash.Value, password);
                return new LoginResult(null, ErrorIds.AuthFailed);
            }

            if (user.LockedUntil is not null && UserRepository.ParseUtc(user.LockedUntil) > now)
            {
                return new LoginResult(null, ErrorIds.AuthLocked);
            }

            var verification = Hasher.VerifyHashedPassword(user, user.PasswordHash, password);
            if (verification == PasswordVerificationResult.Failed)
            {
                var failedCount = user.FailedCount + 1;
                var auth = options.Value;
                // 上限回目の失敗でロックし、回数は 0 に戻す（6 回目から E-AUTH-LOCKED）
                if (failedCount >= auth.MaxFailedAttempts)
                {
                    await UserRepository.RecordFailureAsync(
                        connection, transaction, user.Id, 0, UserRepository.FormatUtc(now.AddMinutes(auth.LockoutMinutes)), nowText).ConfigureAwait(false);
                }
                else
                {
                    await UserRepository.RecordFailureAsync(connection, transaction, user.Id, failedCount, user.LockedUntil, nowText).ConfigureAwait(false);
                }

                return new LoginResult(null, ErrorIds.AuthFailed);
            }

            // 無効化の有無を漏らさないため、パスワードが正しくても同じ E-AUTH-FAILED
            if (!user.IsActive)
            {
                return new LoginResult(null, ErrorIds.AuthFailed);
            }

            await UserRepository.ResetFailuresAsync(connection, transaction, user.Id, nowText).ConfigureAwait(false);
            if (verification == PasswordVerificationResult.SuccessRehashNeeded)
            {
                await UserRepository.UpdatePasswordHashAsync(connection, transaction, user.Id, Hasher.HashPassword(user, password), nowText).ConfigureAwait(false);
            }

            return new LoginResult(user, null);
        }).ConfigureAwait(false);

        if (!result.Succeeded)
        {
            LogLoginFailed(logger, result.ErrorId!, loginId, remoteIp);
        }

        return result;
    }
}
