using System.Data;
using System.Globalization;
using Dapper;
using Microsoft.Data.Sqlite;
using TodoApp.Infrastructure;

namespace TodoApp.Data;

public sealed record MigrationFile(int Version, string Name, string FilePath);

/// <summary>番号付き SQL ファイルの適用（DD-02 §1・ADR 0002）。</summary>
public sealed class Migrator
{
    private const string ForeignKeysOffMarker = "-- requires: foreign_keys=off";

    private readonly Db _db;
    private readonly IClock _clock;

    public Migrator(Db db, IClock clock, string migrationsDirectory)
    {
        _db = db;
        _clock = clock;
        Files = Directory.GetFiles(migrationsDirectory, "*.sql")
            .Select(p => Path.GetFileName(p))
            .Select(n => new MigrationFile(
                int.Parse(n[..4], NumberStyles.None, CultureInfo.InvariantCulture),
                n,
                Path.Combine(migrationsDirectory, n)))
            .OrderBy(f => f.Version)
            .ToList();
    }

    public static string DefaultMigrationsDirectory => Path.Combine(AppContext.BaseDirectory, "migrations");

    public IReadOnlyList<MigrationFile> Files { get; }

    /// <summary>配布物に含まれる最大番号。</summary>
    public int ExpectedSchemaVersion => Files.Count == 0 ? 0 : Files[^1].Version;

    /// <summary>DB の版。schema_version が無ければ 0。</summary>
    public async Task<int> GetCurrentVersionAsync()
    {
        await using var connection = await _db.OpenAsync().ConfigureAwait(false);
        var exists = await connection.ExecuteScalarAsync<long>(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'").ConfigureAwait(false);
        if (exists == 0)
        {
            return 0;
        }

        return await connection.ExecuteScalarAsync<int?>("SELECT MAX(version) FROM schema_version").ConfigureAwait(false) ?? 0;
    }

    public async Task<IReadOnlyList<MigrationFile>> GetPendingAsync()
    {
        var current = await GetCurrentVersionAsync().ConfigureAwait(false);
        return Files.Where(f => f.Version > current).ToList();
    }

    /// <summary>未適用のファイルを番号順に 1 ファイル 1 トランザクションで適用し、適用したファイルを返す。</summary>
    public async Task<IReadOnlyList<MigrationFile>> ApplyAsync()
    {
        var pending = await GetPendingAsync().ConfigureAwait(false);
        await using var connection = await _db.OpenAsync().ConfigureAwait(false);
        await connection.ExecuteAsync("PRAGMA journal_mode=WAL;").ConfigureAwait(false);

        foreach (var file in pending)
        {
            var sql = await File.ReadAllTextAsync(file.FilePath).ConfigureAwait(false);
            var foreignKeysOff = sql.TrimStart().StartsWith(ForeignKeysOffMarker, StringComparison.Ordinal);
            if (foreignKeysOff)
            {
                // PRAGMA foreign_keys はトランザクション内で変えられないため外で切り替える
                await connection.ExecuteAsync("PRAGMA foreign_keys=OFF;").ConfigureAwait(false);
            }

            try
            {
                await using var transaction = connection.BeginTransaction(IsolationLevel.Serializable, deferred: false);
                await connection.ExecuteAsync(sql, transaction: transaction).ConfigureAwait(false);
                if (foreignKeysOff)
                {
                    var violations = (await connection.QueryAsync("PRAGMA foreign_key_check;", transaction: transaction)
                        .ConfigureAwait(false)).Count();
                    if (violations > 0)
                    {
                        throw new InvalidOperationException(
                            $"{file.Name}: foreign_key_check で {violations} 件の違反があるためロールバックしました");
                    }
                }

                await connection.ExecuteAsync(
                    "INSERT INTO schema_version (version, applied_at) VALUES (@Version, @AppliedAt)",
                    new
                    {
                        file.Version,
                        AppliedAt = _clock.UtcNow.UtcDateTime.ToString("yyyy-MM-dd'T'HH:mm:ss.fff'Z'", CultureInfo.InvariantCulture),
                    },
                    transaction).ConfigureAwait(false);
                await transaction.CommitAsync().ConfigureAwait(false);
            }
            finally
            {
                if (foreignKeysOff)
                {
                    await connection.ExecuteAsync("PRAGMA foreign_keys=ON;").ConfigureAwait(false);
                }
            }
        }

        return pending;
    }
}
