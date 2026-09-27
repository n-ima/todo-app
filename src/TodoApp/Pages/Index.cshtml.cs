using Microsoft.AspNetCore.Mvc.RazorPages;
using TodoApp.Services;

namespace TodoApp.Pages;

/// <summary>タスク一覧（ツリー）（DD-11 §2・DD-06 §2〜§4・US-005）。絞り込みバーとクエリの解釈は TASK-109 で足す。</summary>
public sealed class IndexModel(TaskListService service) : PageModel
{
    public TaskListView View { get; private set; } = null!;

    public async Task OnGetAsync()
    {
        ViewData["Nav"] = "list";
        View = await service.BuildAsync(TaskFilter.None, CurrentUser.From(User)).ConfigureAwait(false);
    }
}
