using System.Net;
using System.Text.Json;
using Dapper;
using Microsoft.Data.Sqlite;
using TodoApp.Infrastructure;
using Xunit;

namespace TodoApp.Tests;

/// <summary>業務エラーの /error 変換（TASK-009・NFR-008・DD-12・DD-01 §5）。</summary>
[Collection(WebHostSerial.Name)]
public sealed class AppErrorResponseTests
{
    [Fact]
    public async Task ログイン中のSQLITE_BUSYは画面もAPIも503のE_DB_BUSYになる()
    {
        await using var app = await TestWebApp.CreateAsync();
        var client = await app.CreateLoggedInClientAsync();
        var token = await TestWebApp.GetTokenAsync(client, "/account/password");

        await using var holder = new SqliteConnection(new SqliteConnectionStringBuilder { DataSource = app.DatabasePath }.ToString());
        await holder.OpenAsync(TestContext.Current.CancellationToken);
        await holder.ExecuteAsync("BEGIN IMMEDIATE;");

        using (var content = new FormUrlEncodedContent(new Dictionary<string, string>
        {
            ["__RequestVerificationToken"] = token,
            ["currentPassword"] = "password-1",
            ["newPassword"] = "password-2",
            ["newPasswordConfirm"] = "password-2",
        }))
        using (var page = await client.PostAsync(new Uri("/account/password", UriKind.Relative), content, TestContext.Current.CancellationToken))
        {
            Assert.Equal(HttpStatusCode.ServiceUnavailable, page.StatusCode);
            var html = WebUtility.HtmlDecode(await page.Content.ReadAsStringAsync(TestContext.Current.CancellationToken));
            Assert.Contains(ErrorIds.DbBusy, html, StringComparison.Ordinal);
            Assert.Contains(ErrorIds.Messages[ErrorIds.DbBusy], html, StringComparison.Ordinal);
            Assert.DoesNotContain(ErrorIds.SysUnexpected, html, StringComparison.Ordinal);
        }

        using (var api = await client.GetAsync(new Uri(TestWebApp.ApiDbWritePath, UriKind.Relative), TestContext.Current.CancellationToken))
        {
            Assert.Equal(HttpStatusCode.ServiceUnavailable, api.StatusCode);
            using var json = JsonDocument.Parse(await api.Content.ReadAsStringAsync(TestContext.Current.CancellationToken));
            var error = json.RootElement.GetProperty("error");
            Assert.Equal(ErrorIds.DbBusy, error.GetProperty("id").GetString());
            Assert.Equal(ErrorIds.Messages[ErrorIds.DbBusy], error.GetProperty("message").GetString());
        }

        await holder.ExecuteAsync("ROLLBACK;");
    }
}
