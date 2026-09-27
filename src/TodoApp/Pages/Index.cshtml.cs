using Microsoft.AspNetCore.Mvc.RazorPages;
using TodoApp.Services;

namespace TodoApp.Pages;

/// <summary>タスク一覧（ツリー）（DD-11 §2・DD-06 §2〜§4・US-005）。絞り込みバー・期限超過件数（US-012・US-013）。</summary>
public sealed class IndexModel(TaskListService service) : PageModel
{
    public TaskListView View { get; private set; } = null!;

    public TaskFilter Filter { get; private set; } = TaskFilter.None;

    public async Task OnGetAsync()
    {
        ViewData["Nav"] = "list";
        // ナビの「一覧」「カレンダー」に現在の絞り込みを引き継ぐ（DD-11 §1）
        ViewData["NavQuery"] = Request.QueryString.Value;
        Filter = TaskFilter.FromQuery(Request.Query);
        View = await service.BuildAsync(Filter, CurrentUser.From(User)).ConfigureAwait(false);
    }
}
