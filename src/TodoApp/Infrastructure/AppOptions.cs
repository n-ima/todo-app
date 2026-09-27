namespace TodoApp.Infrastructure;

// DD-01 §7 の設定キー。既定値は appsettings.json が正（ここでは束縛先の型だけを定める）。
public sealed class AuthOptions
{
    public const string Section = "Auth";
    public int MaxFailedAttempts { get; set; }
    public int LockoutMinutes { get; set; }
    public int SessionIdleHours { get; set; }
    public int PasswordMinLength { get; set; }
}

public sealed class LockOptions
{
    public const string Section = "Lock";
    public int IdleMinutes { get; set; }
    public int AutosaveSeconds { get; set; }
}

public sealed class MemoOptions
{
    public const string Section = "Memo";
    public int MaxLength { get; set; }
}

public sealed class TrashOptions
{
    public const string Section = "Trash";
    public int RetentionDays { get; set; }
}

public sealed class BackupOptions
{
    public const string Section = "Backup";
    public int Generations { get; set; }
    public int RetryMinutes { get; set; }
    public int WarnAfterFailedDays { get; set; }
    public string SharePath { get; set; } = "";
}

public sealed class DailyOptions
{
    public const string Section = "Daily";
    public int CheckIntervalMinutes { get; set; }
}

public sealed class LogOptions
{
    public const string Section = "Log";
    public int RetentionDays { get; set; }
}

public sealed class CalendarOptions
{
    public const string Section = "Calendar";
    public int MonthMaxPerDay { get; set; }
}

public sealed class PathsOptions
{
    public const string Section = "Paths";
    public string Data { get; set; } = "";
}

public static class AppConfiguration
{
    public const string LocalSettingsFileName = "appsettings.local.json";

    /// <summary>
    /// Paths:Data 配下の appsettings.local.json を重ねる（版の入れ替えで消えない利用者固有値。DD-01 §7）。
    /// </summary>
    public static void AddLocalSettings(IConfigurationManager configuration)
    {
        var dataPath = configuration[$"{PathsOptions.Section}:Data"];
        if (string.IsNullOrEmpty(dataPath))
        {
            return;
        }

        configuration.AddJsonFile(Path.Combine(dataPath, LocalSettingsFileName), optional: true, reloadOnChange: false);
    }

    public static void AddAppServices(IServiceCollection services, IConfiguration configuration)
    {
        services.Configure<AuthOptions>(configuration.GetSection(AuthOptions.Section));
        services.Configure<LockOptions>(configuration.GetSection(LockOptions.Section));
        services.Configure<MemoOptions>(configuration.GetSection(MemoOptions.Section));
        services.Configure<TrashOptions>(configuration.GetSection(TrashOptions.Section));
        services.Configure<BackupOptions>(configuration.GetSection(BackupOptions.Section));
        services.Configure<DailyOptions>(configuration.GetSection(DailyOptions.Section));
        services.Configure<LogOptions>(configuration.GetSection(LogOptions.Section));
        services.Configure<CalendarOptions>(configuration.GetSection(CalendarOptions.Section));
        services.Configure<PathsOptions>(configuration.GetSection(PathsOptions.Section));
        services.AddSingleton<IClock, SystemClock>();
    }
}
