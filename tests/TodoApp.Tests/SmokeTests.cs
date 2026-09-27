using Xunit;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Data.Sqlite;
using TodoApp.Cli;

namespace TodoApp.Tests;

public sealed class SmokeTests(WebApplicationFactory<Program> factory) : IClassFixture<WebApplicationFactory<Program>>, IDisposable
{
    private readonly string _dir = Path.Combine(Path.GetTempPath(), "todoapp-smoke-" + Guid.NewGuid().ToString("N"));

    public void Dispose()
    {
        SqliteConnection.ClearAllPools();
        if (Directory.Exists(_dir))
        {
            Directory.Delete(_dir, recursive: true);
        }
    }

    [Fact]
    public async Task Host_起動する_未定義パスは404()
    {
        // 起動時に DB の版を確認するため、版の合った DB を用意する（DD-02 §1）
        Assert.Equal(0, await CliRunner.RunAsync(["migrate", "--init", "--data", _dir], TextWriter.Null, TextWriter.Null));
        using var client = factory.WithWebHostBuilder(b => b.UseSetting("Paths:Data", _dir)).CreateClient();
        using var response = await client.GetAsync(new Uri("/", UriKind.Relative), TestContext.Current.CancellationToken);
        Assert.Equal(System.Net.HttpStatusCode.NotFound, response.StatusCode);
    }
}
