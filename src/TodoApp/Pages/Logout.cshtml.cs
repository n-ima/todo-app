using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using TodoApp.Infrastructure;

namespace TodoApp.Pages;

/// <summary>ログアウト（DD-03 §2）。編集ロックの解放は LockService の実装時に接続する（TASK-111）。</summary>
public sealed class LogoutModel : PageModel
{
    public async Task<IActionResult> OnPostAsync()
    {
        await HttpContext.SignOutAsync(CookieAuthenticationDefaults.AuthenticationScheme).ConfigureAwait(false);
        return LocalRedirect(AuthSetup.LoginPath);
    }
}
