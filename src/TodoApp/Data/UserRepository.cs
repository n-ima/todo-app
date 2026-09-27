using System.Data;
using System.Globalization;
using Dapper;

namespace TodoApp.Data;

/// <summary>users の 1 行（DD-02）。PasswordHasher&lt;UserRecord&gt; の型引数を兼ねるため class。</summary>
public sealed class UserRecord
{
    public long Id { get; init; }
    public string LoginId { get; init; } = "";
    public string DisplayName { get; init; } = "";
    public string Role { get; init; } = "";
    public string PasswordHash { get; init; } = "";
    public bool MustChangePassword { get; init; }
    public bool IsActive { get; init; }
    public long FailedCount { get; init; }
    public string? LockedUntil { get; init; }
    public string SecurityStamp { get; init; } = "";
}

/// <summary>users の SQL（パラメータ化のみ。DD-01 §4）。</summary>
public sealed class UserRepository(Db db)
{
    private const string SelectColumns =
        "SELECT id AS Id, login_id AS LoginId, display_name AS DisplayName, role AS Role, password_hash AS PasswordHash, " +
        "must_change_password AS MustChangePassword, is_active AS IsActive, failed_count AS FailedCount, " +
        "locked_until AS LockedUntil, security_stamp AS SecurityStamp FROM users ";

    public static string FormatUtc(DateTimeOffset utc) =>
        utc.UtcDateTime.ToString("yyyy-MM-dd'T'HH:mm:ss.fff'Z'", CultureInfo.InvariantCulture);

    public static DateTimeOffset ParseUtc(string value) =>
        DateTimeOffset.Parse(value, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal);

    public async Task<UserRecord?> FindByIdAsync(long id)
    {
        await using var connection = await db.OpenAsync().ConfigureAwait(false);
        return await connection.QuerySingleOrDefaultAsync<UserRecord>(SelectColumns + "WHERE id = @id", new { id }).ConfigureAwait(false);
    }

    /// <summary>login_id は列の COLLATE NOCASE で大文字小文字を区別せずに一致させる。</summary>
    public static Task<UserRecord?> FindByLoginIdAsync(IDbConnection connection, IDbTransaction transaction, string loginId) =>
        connection.QuerySingleOrDefaultAsync<UserRecord>(SelectColumns + "WHERE login_id = @loginId", new { loginId }, transaction);

    public static Task RecordFailureAsync(IDbConnection connection, IDbTransaction transaction, long id, long failedCount, string? lockedUntil, string now) =>
        connection.ExecuteAsync(
            "UPDATE users SET failed_count = @failedCount, locked_until = @lockedUntil, updated_at = @now WHERE id = @id",
            new { id, failedCount, lockedUntil, now },
            transaction);

    public static Task ResetFailuresAsync(IDbConnection connection, IDbTransaction transaction, long id, string now) =>
        connection.ExecuteAsync(
            "UPDATE users SET failed_count = 0, locked_until = NULL, updated_at = @now WHERE id = @id",
            new { id, now },
            transaction);

    public static Task UpdatePasswordHashAsync(IDbConnection connection, IDbTransaction transaction, long id, string passwordHash, string now) =>
        connection.ExecuteAsync(
            "UPDATE users SET password_hash = @passwordHash, updated_at = @now WHERE id = @id",
            new { id, passwordHash, now },
            transaction);
}
