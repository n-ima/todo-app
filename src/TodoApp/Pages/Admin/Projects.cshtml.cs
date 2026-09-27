using System.Globalization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using TodoApp.Data;
using TodoApp.Infrastructure;
using TodoApp.Services;

namespace TodoApp.Pages.Admin;

/// <summary>
/// プロジェクト管理画面（DD-11・DD-04 §8・US-004・US-008）。/Admin フォルダは管理者ポリシーで保護される。
/// 一覧の「＋プロジェクト」も同じ Create ハンドラへフォーム POST し（Antiforgery はフォーム POST として検証される）、
/// returnToList=true のとき成功後に一覧へ戻る。
/// </summary>
public sealed class ProjectsModel(ProjectService service) : PageModel
{
    // 画面に理由を出して続けるエラー。それ以外の業務エラーは例外ハンドラへ流す
    private static readonly HashSet<string> InputErrors = [ErrorIds.ProjectName, ErrorIds.LockHeldSubtree, ErrorIds.Validation];

    public IReadOnlyList<ProjectRecord> Projects { get; private set; } = [];

    public string? ErrorMessage { get; private set; }

    public string? ErrorId { get; private set; }

    private long ActorId => long.Parse(User.FindFirst(AuthSetup.ClaimSub)!.Value, CultureInfo.InvariantCulture);

    public async Task OnGetAsync()
    {
        ViewData["Nav"] = "admin";
        Projects = await service.ListAsync(ActorId).ConfigureAwait(false);
    }

    public Task<IActionResult> OnPostCreateAsync(string name, bool returnToList) =>
        RunAsync(() => service.CreateAsync(ActorId, name ?? ""), "プロジェクトを作成しました", returnToList);

    public Task<IActionResult> OnPostRenameAsync(long id, string name) =>
        RunAsync(() => service.RenameAsync(ActorId, id, name ?? ""), "プロジェクトの名前を変更しました", false);

    public Task<IActionResult> OnPostDeleteAsync(long id) =>
        RunAsync(() => service.DeleteAsync(ActorId, id), "プロジェクトを削除しました", false);

    private async Task<IActionResult> RunAsync(Func<Task> action, string flash, bool returnToList)
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
        return returnToList ? LocalRedirect("/") : RedirectToPage();
    }
}
