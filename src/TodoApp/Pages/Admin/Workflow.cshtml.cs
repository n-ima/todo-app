using System.Globalization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using TodoApp.Data;
using TodoApp.Infrastructure;
using TodoApp.Services;

namespace TodoApp.Pages.Admin;

/// <summary>状態の定義画面（DD-11・DD-08 §1・US-015）。/Admin フォルダは管理者ポリシーで保護される。</summary>
public sealed class WorkflowModel(WorkflowService service) : PageModel
{
    // 画面に理由を出して続けるエラー。それ以外の業務エラーは例外ハンドラへ流す
    private static readonly HashSet<string> InputErrors =
        [ErrorIds.WfNameDuplicate, ErrorIds.WfInUse, ErrorIds.WfNeedDone, ErrorIds.WfLastStatus, ErrorIds.Validation];

    public IReadOnlyList<StatusRecord> Statuses { get; private set; } = [];

    public string? ErrorMessage { get; private set; }

    public string? ErrorId { get; private set; }

    private long ActorId => long.Parse(User.FindFirst(AuthSetup.ClaimSub)!.Value, CultureInfo.InvariantCulture);

    public async Task OnGetAsync()
    {
        ViewData["Nav"] = "admin";
        Statuses = await service.ListAsync(ActorId).ConfigureAwait(false);
    }

    public Task<IActionResult> OnPostAddAsync(string name) =>
        RunAsync(() => service.AddAsync(ActorId, name ?? ""), "状態を追加しました");

    public Task<IActionResult> OnPostRenameAsync(long id, string name) =>
        RunAsync(() => service.RenameAsync(ActorId, id, name ?? ""), "状態の名前を変更しました");

    public Task<IActionResult> OnPostUpAsync(long id) =>
        RunAsync(() => service.MoveAsync(ActorId, id, up: true), "並び順を変更しました");

    public Task<IActionResult> OnPostDownAsync(long id) =>
        RunAsync(() => service.MoveAsync(ActorId, id, up: false), "並び順を変更しました");

    public Task<IActionResult> OnPostDoneAsync(long id, bool isDone) =>
        RunAsync(() => service.SetDoneAsync(ActorId, id, isDone), "完了扱いを変更しました");

    public Task<IActionResult> OnPostDeleteAsync(long id) =>
        RunAsync(() => service.DeleteAsync(ActorId, id), "状態を削除しました");

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
