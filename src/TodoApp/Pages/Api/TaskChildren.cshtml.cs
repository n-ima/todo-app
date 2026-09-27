using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using TodoApp.Services;

namespace TodoApp.Pages.Api;

/// <summary>GET /api/tasks/{id}/children: 折りたたまれた行の直下の子の行 HTML（部分ビュー _TaskRows。DD-06 §3）。</summary>
public sealed class TaskChildrenModel(TaskListService service) : PageModel
{
    [FromRoute]
    public long Id { get; set; }

    public IReadOnlyList<TaskListItem> Rows { get; private set; } = [];

    public async Task OnGetAsync()
    {
        Rows = await service.ChildrenAsync(Id, TaskFilter.None, CurrentUser.From(User)).ConfigureAwait(false);
    }
}
