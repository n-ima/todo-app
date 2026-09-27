using System.Globalization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using TodoApp.Infrastructure;
using TodoApp.Services;

namespace TodoApp.Pages.Account;

/// <summary>パスワード変更画面（DD-03 §3・US-002）。</summary>
public sealed class PasswordModel(AuthService authService) : PageModel
{
    [BindProperty]
    public string CurrentPassword { get; set; } = "";

    [BindProperty]
    public string NewPassword { get; set; } = "";

    [BindProperty]
    public string NewPasswordConfirm { get; set; } = "";

    public string? ErrorId { get; private set; }

    /// <summary>初回（初期パスワードのまま）なら説明を出しナビを隠す。</summary>
    public bool IsFirstChange => User.FindFirst(AuthSetup.ClaimMustChange)?.Value == "1";

    public void OnGet()
    {
    }

    public async Task<IActionResult> OnPostAsync()
    {
        var userId = long.Parse(User.FindFirst(AuthSetup.ClaimSub)!.Value, CultureInfo.InvariantCulture);
        try
        {
            var user = await authService.ChangePasswordAsync(userId, CurrentPassword ?? "", NewPassword ?? "", NewPasswordConfirm ?? "").ConfigureAwait(false);
            // 新しい stamp で Cookie を再発行する（本人は継続・他端末は次の要求で失効）
            await AuthSetup.SignInAsync(HttpContext, user).ConfigureAwait(false);
            return LocalRedirect("/");
        }
        catch (AppErrorException ex) when (ex.ErrorId is ErrorIds.PwdCurrent or ErrorIds.PwdLength or ErrorIds.PwdConfirm or ErrorIds.PwdSame)
        {
            ErrorId = ex.ErrorId;
            return Page();
        }
    }
}
