using System.Globalization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using TodoApp.Infrastructure;
using TodoApp.Services;

namespace TodoApp.Pages.Tasks;

/// <summary>
/// タスクの新規作成（DD-11 §2・DD-04 §3・US-004）。/projects/{projectId}/tasks/new（プロジェクト直下）と
/// /tasks/{parentId}/children/new（子タスク）の 2 画面を共通で扱う。POST 先はそれぞれ /projects/{projectId}/tasks・/tasks/{parentId}/children。
/// </summary>
public sealed class NewModel(TaskService service) : PageModel
{
    // 画面に理由を出して続けるエラー。親が無い（E-TASK-PARENT-GONE）等は例外ハンドラへ流す
    private static readonly HashSet<string> InputErrors =
    [
        ErrorIds.TaskTitleRequired, ErrorIds.TaskTitleLength, ErrorIds.TaskDateOrder, ErrorIds.TaskAssigneeInactive, ErrorIds.Validation,
    ];

    [FromRoute]
    public long? ProjectId { get; set; }

    [FromRoute]
    public long? ParentId { get; set; }

    public NewTaskContext Context { get; private set; } = null!;

    public string? ErrorMessage { get; private set; }

    public string? ErrorId { get; private set; }

    [BindProperty]
    public string? Title { get; set; }

    [BindProperty]
    public string? AssigneeId { get; set; }

    [BindProperty]
    public string? StartDate { get; set; }

    [BindProperty]
    public string? DueDate { get; set; }

    [BindProperty]
    public string? Priority { get; set; }

    public string PostPath => ParentId is { } p
        ? $"/tasks/{p.ToString(CultureInfo.InvariantCulture)}/children"
        : $"/projects/{Context.ProjectId.ToString(CultureInfo.InvariantCulture)}/tasks";

    private CurrentUser Actor => CurrentUser.From(User);

    public async Task OnGetAsync()
    {
        Context = await service.GetNewContextAsync(Actor, ProjectId, ParentId).ConfigureAwait(false);
    }

    public async Task<IActionResult> OnPostAsync()
    {
        long id;
        try
        {
            var input = new TaskInput(
                Title ?? "",
                ParseOptionalId(AssigneeId),
                StartDate,
                DueDate,
                string.IsNullOrEmpty(Priority) ? 2 : ParseInt(Priority, "優先度"));
            id = await service.CreateAsync(Actor, ProjectId, ParentId, input).ConfigureAwait(false);
        }
        catch (AppErrorException ex) when (InputErrors.Contains(ex.ErrorId))
        {
            ErrorId = ex.ErrorId;
            ErrorMessage = ex.Message;
            await OnGetAsync().ConfigureAwait(false);
            return Page();
        }

        TempData["Flash"] = "タスクを作成しました";
        return LocalRedirect($"/tasks/{id.ToString(CultureInfo.InvariantCulture)}");
    }

    private static long? ParseOptionalId(string? value) =>
        string.IsNullOrEmpty(value) ? null
        : long.TryParse(value, NumberStyles.None, CultureInfo.InvariantCulture, out var id) ? id
        : throw new AppErrorException(ErrorIds.Validation, "担当者");

    private static int ParseInt(string value, string label) =>
        int.TryParse(value, NumberStyles.None, CultureInfo.InvariantCulture, out var n) ? n : throw new AppErrorException(ErrorIds.Validation, label);
}
