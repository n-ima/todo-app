using System.Globalization;
using System.Security.Claims;
using TodoApp.Infrastructure;

namespace TodoApp.Services;

/// <summary>操作者（DD-01 §2。サービスの公開メソッドの第 1 引数）。</summary>
public sealed record CurrentUser(long Id, string Role, string DisplayName)
{
    public bool IsAdmin => Role == UserAdminService.RoleAdmin;

    /// <summary>ログイン時に発行したクレーム（AuthSetup.CreatePrincipal）から作る。</summary>
    public static CurrentUser From(ClaimsPrincipal principal)
    {
        ArgumentNullException.ThrowIfNull(principal);
        return new(
            long.Parse(principal.FindFirst(AuthSetup.ClaimSub)!.Value, CultureInfo.InvariantCulture),
            principal.FindFirst(AuthSetup.ClaimRole)!.Value,
            principal.FindFirst(AuthSetup.ClaimName)!.Value);
    }
}
