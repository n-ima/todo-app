using System.Globalization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using TodoApp.Data;
using TodoApp.Infrastructure;
using TodoApp.Services;

namespace TodoApp.Pages.Admin;

/// <summary>利用者管理画面（DD-11・DD-03 §4・US-003）。/Admin フォルダは管理者ポリシーで保護される。</summary>
public sealed class UsersModel(UserAdminService service) : PageModel
{
    // 入力の誤り（画面に理由を出して続ける）。それ以外の業務エラーは例外ハンドラへ流す
    private static readonly HashSet<string> InputErrors =
        [ErrorIds.UserDuplicate, ErrorIds.UserLoginidFormat, ErrorIds.UserLastAdmin, ErrorIds.PwdLength, ErrorIds.Validation];

    public IReadOnlyList<UserRecord> Users { get; private set; } = [];

    public string? ErrorMessage { get; private set; }

    public string? ErrorId { get; private set; }

    private long ActorId => long.Parse(User.FindFirst(AuthSetup.ClaimSub)!.Value, CultureInfo.InvariantCulture);

    public async Task OnGetAsync()
    {
        ViewData["Nav"] = "admin";
        Users = await service.ListAsync(ActorId).ConfigureAwait(false);
    }

    public Task<IActionResult> OnPostCreateAsync(string loginId, string displayName, string role, string password) =>
        RunAsync(() => service.CreateAsync(ActorId, loginId ?? "", displayName ?? "", role ?? "", password ?? ""), "利用者を登録しました");

    public Task<IActionResult> OnPostUpdateAsync(long id, string displayName, string role) =>
        RunAsync(() => service.UpdateAsync(ActorId, id, displayName ?? "", role ?? ""), "利用者を変更しました");

    public Task<IActionResult> OnPostDeactivateAsync(long id) =>
        RunAsync(() => service.SetActiveAsync(ActorId, id, false), "利用者を無効化しました");

    public Task<IActionResult> OnPostActivateAsync(long id) =>
        RunAsync(() => service.SetActiveAsync(ActorId, id, true), "利用者を有効化しました");

    public Task<IActionResult> OnPostResetPasswordAsync(long id, string password) =>
        RunAsync(() => service.ResetPasswordAsync(ActorId, id, password ?? ""), "初期パスワードを設定しました");

    private async Task<IActionResult> RunAsync(Func<Task> action, string flash)
    {
        try
        {
            await action().ConfigureAwait(false);
        }
        catch (AppErrorException ex) when (InputErrors.Contains(ex.ErrorId))
        {
            ErrorId = ex.ErrorId;
            ErrorMessage = ex.Message;
            await OnGetAsync().ConfigureAwait(false);
            return Page();
        }

        TempData["Flash"] = flash;
        return RedirectToPage();
    }
}
