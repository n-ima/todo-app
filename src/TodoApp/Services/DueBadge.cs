namespace TodoApp.Services;

/// <summary>期限の強調の区分（DD-06 §6・US-013）。</summary>
public enum DueState
{
    None,
    Soon,
    Overdue,
}

/// <summary>期限の強調の判定。一覧・詳細・カレンダーで共通（部分ビュー _DueBadge と対）。</summary>
public static class DueBadge
{
    /// <summary>today は IClock.Today（日本時間）。完了扱いには付けない。期限間近は今日と明日。</summary>
    public static DueState Classify(DateOnly? due, bool isDone, DateOnly today) => (due, isDone) switch
    {
        (null, _) or (_, true) => DueState.None,
        ({ } d, _) when d < today => DueState.Overdue,
        ({ } d, _) when d <= today.AddDays(1) => DueState.Soon,
        _ => DueState.None,
    };
}
