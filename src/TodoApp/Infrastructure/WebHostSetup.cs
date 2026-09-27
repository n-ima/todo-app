using System.Net;
using Serilog;
using Serilog.Events;
using TodoApp.Cli;
using TodoApp.Data;

namespace TodoApp.Infrastructure;

/// <summary>Web ホストの共通設定（DD-01 §5・§6・§8）。</summary>
public static class WebHostSetup
{
    public const string HealthPath = "/healthz";
    public const string ErrorPath = "/error";

    private const string ContentSecurityPolicy =
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'";

    public static WebApplicationOptions CreateOptions(string[] args) => new()
    {
        Args = args,
        // サービスとして動くときの作業ディレクトリは System32 のため、exe の場所を基準にする
        ContentRootPath = Microsoft.Extensions.Hosting.WindowsServices.WindowsServiceHelpers.IsWindowsService() ? AppContext.BaseDirectory : null,
    };

    public static void ConfigureServices(WebApplicationBuilder builder)
    {
        builder.Services.AddWindowsService(o => o.ServiceName = "TodoApp");
        builder.Services.AddRazorPages();
        // Paths:Data はテスト等で Build 時に差し替わるため、構成の確定後に読む
        builder.Services.AddSerilog((services, lc) =>
        {
            var configuration = services.GetRequiredService<IConfiguration>();
            var dataDirectory = configuration[$"{PathsOptions.Section}:Data"] ?? CliRunner.DefaultDataDirectory;
            lc.MinimumLevel.Information()
                .MinimumLevel.Override("Microsoft.AspNetCore", LogEventLevel.Warning)
                .Enrich.FromLogContext()
                // 日付はサーバーのローカル時刻（日本時間）で切り替わる。保持期間の削除は日次処理（DD-09）
                .WriteTo.File(
                    Path.Combine(dataDirectory, "logs", "app-.log"),
                    rollingInterval: RollingInterval.Day,
                    retainedFileCountLimit: null,
                    formatProvider: System.Globalization.CultureInfo.InvariantCulture);
        });
    }

    public static void ConfigurePipeline(WebApplication app)
    {
        app.Use(SecurityHeaders);
        // ログ出力と画面/JSON の出し分けは /error（Pages/Error.cshtml.cs）が行う
        app.UseExceptionHandler(ErrorPath);
        app.Use(RedirectToHttps);
        app.UseSerilogRequestLogging(o =>
        {
            // クエリ文字列は /tasks 系だけ記録する（DD-01 §8）。ヘッダー・Cookie・本文は記録しない
            o.MessageTemplate = "HTTP {RequestMethod} {RequestPath}{RequestQuery} responded {StatusCode} in {Elapsed:0.0000} ms";
            o.EnrichDiagnosticContext = (diagnostic, context) =>
                diagnostic.Set("RequestQuery", context.Request.Path.StartsWithSegments("/tasks", StringComparison.OrdinalIgnoreCase)
                    ? context.Request.QueryString.Value ?? ""
                    : "");
        });
        app.UseStaticFiles();
        app.Use(NoStore);
        app.MapGet(HealthPath, HealthAsync);
        app.MapRazorPages();
    }

    private static Task SecurityHeaders(HttpContext context, RequestDelegate next)
    {
        // 例外ハンドラは応答ヘッダーを消すため、送信直前に付ける（エラー画面にも CSP を効かせる）
        context.Response.OnStarting(() =>
        {
            var headers = context.Response.Headers;
            headers.ContentSecurityPolicy = ContentSecurityPolicy;
            headers.XContentTypeOptions = "nosniff";
            headers["Referrer-Policy"] = "no-referrer";
            return Task.CompletedTask;
        });
        return next(context);
    }

    private static Task NoStore(HttpContext context, RequestDelegate next)
    {
        context.Response.OnStarting(() =>
        {
            context.Response.Headers.CacheControl = "no-store";
            return Task.CompletedTask;
        });
        return next(context);
    }

    private static bool IsLoopback(HttpContext context) =>
        context.Connection.RemoteIpAddress is { } ip && IPAddress.IsLoopback(ip);

    private static Task RedirectToHttps(HttpContext context, RequestDelegate next)
    {
        if (context.Request.IsHttps || (context.Request.Path.Equals(HealthPath, StringComparison.OrdinalIgnoreCase) && IsLoopback(context)))
        {
            return next(context);
        }

        // 443 以外へは転送しないため、ポートを落とした Host に向ける（DD-01 §6）
        var request = context.Request;
        context.Response.StatusCode = StatusCodes.Status301MovedPermanently;
        context.Response.Headers.Location = $"https://{request.Host.Host}{request.PathBase}{request.Path}{request.QueryString}";
        return Task.CompletedTask;
    }

    private static async Task<IResult> HealthAsync(HttpContext context, IConfiguration configuration)
    {
        if (!IsLoopback(context))
        {
            return Results.NotFound();
        }

        var mismatch = await StartupSchemaCheck.GetMismatchMessageAsync(
            configuration[$"{PathsOptions.Section}:Data"] ?? CliRunner.DefaultDataDirectory, Migrator.DefaultMigrationsDirectory).ConfigureAwait(false);
        return mismatch is null ? Results.Text("OK") : Results.StatusCode(StatusCodes.Status503ServiceUnavailable);
    }
}

/// <summary>JSON API の失敗応答の形（DD-01 §5）: { "error": { "id", "message", "data" } }。</summary>
public static class ErrorResponse
{
    public static Task WriteJsonAsync(HttpContext context, int statusCode, string errorId, string message, object? data = null)
    {
        context.Response.StatusCode = statusCode;
        return context.Response.WriteAsJsonAsync(new { error = new { id = errorId, message, data } });
    }
}
