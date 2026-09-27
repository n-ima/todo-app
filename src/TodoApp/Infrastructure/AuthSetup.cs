using System.Globalization;
using System.Security.Claims;
using Microsoft.AspNetCore.Antiforgery;
using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.Core.Infrastructure;
using Microsoft.AspNetCore.Mvc.Filters;
using Microsoft.AspNetCore.Mvc.ModelBinding;
using Microsoft.AspNetCore.Mvc.ViewFeatures;
using Microsoft.Extensions.Options;
using TodoApp.Data;

namespace TodoApp.Infrastructure;

/// <summary>Cookie 認証・既定の認可・Antiforgery の失敗応答（DD-03 §1・DD-01 §6）。</summary>
public static class AuthSetup
{
    public const string CookieName = "TodoApp.Auth";
    public const string LoginPath = "/login";
    public const string ClaimSub = "sub";
    public const string ClaimName = "name";
    public const string ClaimRole = "role";
    public const string ClaimStamp = "stamp";
    public const string ClaimMustChange = "mcp";

    // 操作に数えない要求（セッションを延長しない）。静的ファイルは認証より前で返るため含めない
    private static readonly string[] NoRenewPaths = [WebHostSetup.HealthPath, "/api/markdown/preview"];

    public static void ConfigureServices(IServiceCollection services)
    {
        services.AddAuthentication(CookieAuthenticationDefaults.AuthenticationScheme).AddCookie();
        services.AddOptions<CookieAuthenticationOptions>(CookieAuthenticationDefaults.AuthenticationScheme)
            .Configure<IOptions<AuthOptions>, IClock>((o, auth, clock) =>
            {
                o.Cookie.Name = CookieName;
                o.Cookie.HttpOnly = true;
                o.Cookie.SecurePolicy = CookieSecurePolicy.Always;
                o.Cookie.SameSite = SameSiteMode.Strict;
                o.ExpireTimeSpan = TimeSpan.FromHours(auth.Value.SessionIdleHours);
                o.SlidingExpiration = true;
                o.LoginPath = LoginPath;
                o.ReturnUrlParameter = "returnUrl";
                o.TimeProvider = new ClockTimeProvider(clock);
                o.Events.OnValidatePrincipal = ValidatePrincipalAsync;
                o.Events.OnRedirectToLogin = RedirectToLoginAsync;
                o.Events.OnCheckSlidingExpiration = c =>
                {
                    // 標準のスライディング（残り半分で更新）も、操作に数えない要求では行わない
                    c.ShouldRenew = c.ShouldRenew && !IsNoRenewPath(c.HttpContext.Request.Path);
                    return Task.CompletedTask;
                };
            });
        // 全ページ・全 API を要ログインにする。例外は [AllowAnonymous] を付けた /login・/error・/healthz
        services.AddAuthorizationBuilder().SetFallbackPolicy(new AuthorizationPolicyBuilder().RequireAuthenticatedUser().Build());
    }

    public static ClaimsPrincipal CreatePrincipal(UserRecord user)
    {
        ArgumentNullException.ThrowIfNull(user);
        var identity = new ClaimsIdentity(
            [
                new Claim(ClaimSub, user.Id.ToString(CultureInfo.InvariantCulture)),
                new Claim(ClaimName, user.DisplayName),
                new Claim(ClaimRole, user.Role),
                new Claim(ClaimStamp, user.SecurityStamp),
                new Claim(ClaimMustChange, user.MustChangePassword ? "1" : "0"),
            ],
            CookieAuthenticationDefaults.AuthenticationScheme,
            ClaimName,
            ClaimRole);
        return new ClaimsPrincipal(identity);
    }

    /// <summary>ログイン・再発行とも永続化しない（ブラウザのセッション Cookie）。</summary>
    public static Task SignInAsync(HttpContext context, UserRecord user) =>
        context.SignInAsync(CookieAuthenticationDefaults.AuthenticationScheme, CreatePrincipal(user), new AuthenticationProperties { IsPersistent = false });

    public static bool IsApiPath(PathString path) => path.StartsWithSegments("/api", StringComparison.OrdinalIgnoreCase);

    private static async Task ValidatePrincipalAsync(CookieValidatePrincipalContext context)
    {
        var sub = context.Principal?.FindFirst(ClaimSub)?.Value;
        var stamp = context.Principal?.FindFirst(ClaimStamp)?.Value;
        var user = long.TryParse(sub, NumberStyles.None, CultureInfo.InvariantCulture, out var id)
            ? await context.HttpContext.RequestServices.GetRequiredService<UserRepository>().FindByIdAsync(id).ConfigureAwait(false)
            : null;
        // 無効化・パスワード再設定・ロール変更（stamp 更新）を次の要求で効かせる
        if (user is null || !user.IsActive || !string.Equals(user.SecurityStamp, stamp, StringComparison.Ordinal))
        {
            context.RejectPrincipal();
            await context.HttpContext.SignOutAsync(CookieAuthenticationDefaults.AuthenticationScheme).ConfigureAwait(false);
            return;
        }

        context.ReplacePrincipal(CreatePrincipal(user));
        // 標準のスライディングは残り半分を切るまで更新しないため、操作のたびに延長する（最後の操作から 8 時間）
        context.ShouldRenew = !IsNoRenewPath(context.HttpContext.Request.Path);
    }

    private static bool IsNoRenewPath(PathString path) => NoRenewPaths.Any(p => path.Equals(p, StringComparison.OrdinalIgnoreCase));

    private static Task RedirectToLoginAsync(RedirectContext<CookieAuthenticationOptions> context)
    {
        if (IsApiPath(context.Request.Path))
        {
            return ErrorResponse.WriteJsonAsync(
                context.HttpContext, StatusCodes.Status401Unauthorized, ErrorIds.AuthRequired, ErrorIds.Messages[ErrorIds.AuthRequired]);
        }

        // 既定の RedirectUri は Host ヘッダー由来の絶対 URL のため、パスとクエリだけで転送する
        context.Response.Redirect(new Uri(context.RedirectUri, UriKind.RelativeOrAbsolute) is { IsAbsoluteUri: true } absolute
            ? absolute.PathAndQuery
            : context.RedirectUri);
        return Task.CompletedTask;
    }

    public const string PasswordPath = "/account/password";
    public const string LogoutPath = "/logout";

    /// <summary>
    /// 初期パスワードのままの利用者は /account/password・/logout・/error 以外を変更画面へ 302、API は 403 E-AUTH-MUST-CHANGE（DD-03 §3）。
    /// 静的ファイルはこのミドルウェアより前で返る。クレームは ValidatePrincipalAsync が毎要求 DB の値に置き換える。
    /// </summary>
    public static Task ForcePasswordChange(HttpContext context, RequestDelegate next)
    {
        ArgumentNullException.ThrowIfNull(context);
        ArgumentNullException.ThrowIfNull(next);
        var path = context.Request.Path;
        if (context.User.FindFirst(ClaimMustChange)?.Value != "1"
            || path.Equals(PasswordPath, StringComparison.OrdinalIgnoreCase)
            || path.Equals(LogoutPath, StringComparison.OrdinalIgnoreCase)
            // 例外ハンドラの再実行先。転送すると想定外例外が 500 画面にならない
            || path.Equals(WebHostSetup.ErrorPath, StringComparison.OrdinalIgnoreCase))
        {
            return next(context);
        }

        if (IsApiPath(path))
        {
            return ErrorResponse.WriteJsonAsync(
                context, StatusCodes.Status403Forbidden, ErrorIds.AuthMustChange, ErrorIds.Messages[ErrorIds.AuthMustChange]);
        }

        context.Response.Redirect(PasswordPath);
        return Task.CompletedTask;
    }

    /// <summary>API（/api/*）の状態変更要求は RequestVerificationToken ヘッダーを必須にする。失敗は 400 E-CSRF の JSON。</summary>
    public static async Task ApiAntiforgery(HttpContext context, RequestDelegate next)
    {
        ArgumentNullException.ThrowIfNull(context);
        ArgumentNullException.ThrowIfNull(next);
        var method = context.Request.Method;
        if (IsApiPath(context.Request.Path)
            && !(HttpMethods.IsGet(method) || HttpMethods.IsHead(method) || HttpMethods.IsOptions(method) || HttpMethods.IsTrace(method))
            && !await context.RequestServices.GetRequiredService<IAntiforgery>().IsRequestValidAsync(context).ConfigureAwait(false))
        {
            await ErrorResponse.WriteJsonAsync(context, StatusCodes.Status400BadRequest, ErrorIds.Csrf, ErrorIds.Messages[ErrorIds.Csrf]).ConfigureAwait(false);
            return;
        }

        await next(context).ConfigureAwait(false);
    }
}

/// <summary>Cookie の有効期限判定も IClock で行う（テストで 8 時間の経過を再現するため）。</summary>
public sealed class ClockTimeProvider(IClock clock) : TimeProvider
{
    public override DateTimeOffset GetUtcNow() => clock.UtcNow;
}

/// <summary>画面（フォーム POST）の Antiforgery 検証失敗を 400 の E-CSRF 画面にする（DD-12）。</summary>
public sealed class CsrfFailureFilter : IAlwaysRunResultFilter
{
    public const string ViewPath = "/Pages/Shared/_CsrfError.cshtml";

    public void OnResultExecuting(ResultExecutingContext context)
    {
        ArgumentNullException.ThrowIfNull(context);
        if (context.Result is IAntiforgeryValidationFailedResult)
        {
            context.Result = new ViewResult
            {
                ViewName = ViewPath,
                StatusCode = StatusCodes.Status400BadRequest,
                ViewData = new ViewDataDictionary(new EmptyModelMetadataProvider(), context.ModelState),
            };
        }
    }

    public void OnResultExecuted(ResultExecutedContext context)
    {
    }
}
