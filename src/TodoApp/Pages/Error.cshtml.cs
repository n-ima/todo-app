using System.Globalization;
using Microsoft.AspNetCore.Diagnostics;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using TodoApp.Infrastructure;

namespace TodoApp.Pages;

/// <summary>
/// 想定外の例外の画面（DD-11 §2・DD-01 §5）。例外ハンドラが元の要求メソッドのまま再実行するため GET/POST の両方で応答する。
/// </summary>
[IgnoreAntiforgeryToken]
public sealed partial class ErrorModel(ILoggerFactory loggerFactory) : PageModel
{
    public string ErrorId { get; private set; } = ErrorIds.SysUnexpected;

    public string Message { get; private set; } = "";

    public string CorrelationId { get; private set; } = "";

    [LoggerMessage(Level = LogLevel.Error, Message = "{ErrorId} 相関 ID: {CorrelationId}")]
    private static partial void LogUnexpected(ILogger logger, Exception? exception, string errorId, string correlationId);

    public Task<IActionResult> OnGetAsync() => HandleAsync();

    public Task<IActionResult> OnPostAsync() => HandleAsync();

    private async Task<IActionResult> HandleAsync()
    {
        CorrelationId = HttpContext.TraceIdentifier;
        Message = string.Format(CultureInfo.InvariantCulture, ErrorIds.Messages[ErrorIds.SysUnexpected], CorrelationId);
        Response.StatusCode = StatusCodes.Status500InternalServerError;

        var feature = HttpContext.Features.Get<IExceptionHandlerPathFeature>();
        if (feature is not null)
        {
            LogUnexpected(loggerFactory.CreateLogger("TodoApp.UnhandledException"), feature.Error, ErrorIds.SysUnexpected, CorrelationId);
            if (feature.Path.StartsWith("/api", StringComparison.OrdinalIgnoreCase)
                && (feature.Path.Length == 4 || feature.Path[4] == '/'))
            {
                await ErrorResponse.WriteJsonAsync(HttpContext, StatusCodes.Status500InternalServerError, ErrorIds.SysUnexpected, Message).ConfigureAwait(false);
                return new EmptyResult();
            }
        }

        // 例外の詳細（スタックトレース）は画面に出さない
        return Page();
    }
}
