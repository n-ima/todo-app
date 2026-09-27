namespace TodoApp.Infrastructure;

/// <summary>時刻の唯一の取得口（DD-01 §3）。テストで差し替えるため DateTime.Now を直接使わない。</summary>
public interface IClock
{
    DateTimeOffset UtcNow { get; }

    /// <summary>Tokyo Standard Time の暦日。</summary>
    DateOnly Today { get; }
}

public static class TokyoTime
{
    public static readonly TimeZoneInfo Zone = TimeZoneInfo.FindSystemTimeZoneById("Tokyo Standard Time");

    public static DateOnly ToTokyoDate(DateTimeOffset utc) =>
        DateOnly.FromDateTime(TimeZoneInfo.ConvertTime(utc, Zone).DateTime);
}

public sealed class SystemClock : IClock
{
    public DateTimeOffset UtcNow => DateTimeOffset.UtcNow;

    public DateOnly Today => TokyoTime.ToTokyoDate(UtcNow);
}
