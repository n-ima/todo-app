using System.Net;
using System.Text.RegularExpressions;
using TodoApp.Infrastructure;
using Xunit;

namespace TodoApp.Tests;

/// <summary>共通レイアウトと静的資産のレンダリング結合テスト（TASK-007・DD-11 §1・§4・§5）。</summary>
[Collection(WebHostSerial.Name)]
public sealed partial class LayoutTests
{
    private static Uri U(string path) => new(path, UriKind.Relative);

    private static CancellationToken Ct => TestContext.Current.CancellationToken;

    [GeneratedRegex(@"<script(?![^>]*\ssrc=)[^>]*>", RegexOptions.IgnoreCase)]
    private static partial Regex InlineScript();

    [GeneratedRegex(@"\s(style|on[a-z]+)\s*=", RegexOptions.IgnoreCase)]
    private static partial Regex InlineStyleOrHandler();

    private static async Task<string> GetErrorPageAsync(TestWebApp app, string path)
    {
        // 想定外例外の経路は要ログイン（既定の認可）のため、ログインしてから開く
        using var client = await app.CreateLoggedInClientAsync();
        using var response = await client.GetAsync(U(path), Ct);
        Assert.Equal(HttpStatusCode.InternalServerError, response.StatusCode);
        return await response.Content.ReadAsStringAsync(Ct);
    }

    [Fact]
    public async Task レイアウト_csrfとautosaveのmetaを出す()
    {
        await using var app = await TestWebApp.CreateAsync();
        var body = await GetErrorPageAsync(app, "/error");

        Assert.Matches("<meta name=\"csrf-token\" content=\"[^\"]{20,}\"", body);
        Assert.Contains("<meta name=\"autosave-seconds\" content=\"60\"", body, StringComparison.Ordinal);
        Assert.Contains("<link rel=\"stylesheet\" href=\"/css/app.css\"", body, StringComparison.Ordinal);
        Assert.Contains("<script type=\"module\" src=\"/js/confirm.js\"></script>", body, StringComparison.Ordinal);
        Assert.Contains("TODO アプリ", body, StringComparison.Ordinal);
    }

    [Theory]
    [InlineData("/error")]
    [InlineData(TestWebApp.ThrowPath)]
    public async Task レイアウト_インラインのscript_style_onは無い(string path)
    {
        await using var app = await TestWebApp.CreateAsync();
        var body = await GetErrorPageAsync(app, path);

        Assert.DoesNotMatch(InlineScript(), body);
        Assert.DoesNotMatch(InlineStyleOrHandler(), body);
        Assert.DoesNotContain("<style", body, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public async Task エラー画面_想定外例外_文言とエラーIDと相関IDと一覧へ戻る()
    {
        await using var app = await TestWebApp.CreateAsync();
        var body = await GetErrorPageAsync(app, TestWebApp.ThrowPath);

        Assert.Contains($"（{ErrorIds.SysUnexpected}）", body, StringComparison.Ordinal);
        var correlationId = Regex.Match(body, "相関 ID: <code>([^<]+)</code>").Groups[1].Value;
        Assert.NotEmpty(correlationId);
        Assert.Contains("<a href=\"/\">一覧へ戻る</a>", body, StringComparison.Ordinal);
        Assert.Contains("<meta name=\"csrf-token\"", body, StringComparison.Ordinal);
    }

    [Fact]
    public async Task エラー画面_POSTの想定外例外でも500のエラー画面()
    {
        await using var app = await TestWebApp.CreateAsync();
        using var client = await app.CreateLoggedInClientAsync();
        using var content = new FormUrlEncodedContent([]);
        using var response = await client.PostAsync(U(TestWebApp.ThrowPath), content, Ct);
        var body = await response.Content.ReadAsStringAsync(Ct);

        Assert.Equal(HttpStatusCode.InternalServerError, response.StatusCode);
        Assert.Contains(ErrorIds.SysUnexpected, body, StringComparison.Ordinal);
        Assert.DoesNotContain(TestWebApp.ThrowMarker, body, StringComparison.Ordinal);
    }

    [Fact]
    public async Task 静的資産_app_cssはトークンと768pxのメディアクエリを持つ()
    {
        await using var app = await TestWebApp.CreateAsync();
        using var client = app.CreateClient();
        using var response = await client.GetAsync(U("/css/app.css"), Ct);
        var css = await response.Content.ReadAsStringAsync(Ct);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        foreach (var token in new[] { "--bg: #ffffff", "--danger: #b42318", "--warn-mark: #b45309", "--text-dim: #6b7280", "--indent: 20px", "--focus: 2px solid #1d4ed8" })
        {
            Assert.Contains(token, css, StringComparison.Ordinal);
        }

        Assert.Contains("@media (max-width: 767px)", css, StringComparison.Ordinal);
        Assert.Contains("--indent: 12px", css, StringComparison.Ordinal);
    }

    [Fact]
    public async Task 静的資産_confirm_jsはESモジュールでopenDialogを公開()
    {
        await using var app = await TestWebApp.CreateAsync();
        using var client = app.CreateClient();
        using var response = await client.GetAsync(U("/js/confirm.js"), Ct);
        var js = await response.Content.ReadAsStringAsync(Ct);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Contains("export function openDialog", js, StringComparison.Ordinal);
        Assert.Contains("showModal()", js, StringComparison.Ordinal);
    }
}
