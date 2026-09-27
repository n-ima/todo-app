using TodoApp.Infrastructure;

namespace TodoApp.Tests;

/// <summary>テスト用の固定時計。</summary>
public sealed class FakeClock(DateTimeOffset utcNow) : IClock
{
    public DateTimeOffset UtcNow { get; set; } = utcNow;

    public DateOnly Today => TokyoTime.ToTokyoDate(UtcNow);
}
