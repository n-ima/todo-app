using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using TodoApp.Infrastructure;
using TodoApp.Services;

namespace TodoApp.Pages;

/// <summary>ログイン画面（DD-03 §2・US-001）。</summary>
[AllowAnonymous]
public sealed class LoginModel(AuthService authService) : PageModel
{
    [BindProperty(SupportsGet = true)]
    public string? ReturnUrl { get; set; }

    [BindProperty]
    public string LoginId { get; set; } = "";

    [BindProperty]
    public string Password { get; set; } = "";

    public string? ErrorId { get; private set; }

    public void OnGet()
    {
    }

    public async Task<IActionResult> OnPostAsync()
    {
        LoginId ??= "";
        Password ??= "";
        // 入力の形式（1〜50 / 1〜128 文字）外は照合するまでもなく失敗。どちらが誤りかは区別しない
        if (LoginId.Length is < 1 or > 50 || Password.Length is < 1 or > 128)
        {
            ErrorId = ErrorIds.AuthFailed;
            return Page();
        }

        var result = await authService.LoginAsync(LoginId, Password, HttpContext.Connection.RemoteIpAddress?.ToString() ?? "").ConfigureAwait(false);
        if (!result.Succeeded)
        {
            ErrorId = result.ErrorId;
            return Page();
        }

        // 新しい Cookie を発行する（セッション固定対策）
        await AuthSetup.SignInAsync(HttpContext, result.User!).ConfigureAwait(false);
        if (result.User!.MustChangePassword)
        {
            return LocalRedirect(AuthSetup.PasswordPath);
        }

        // 外部 URL へは転送しない（オープンリダイレクト対策）
        return LocalRedirect(Url.IsLocalUrl(ReturnUrl) ? ReturnUrl! : "/");
    }
}
