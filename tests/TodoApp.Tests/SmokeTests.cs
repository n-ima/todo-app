using Xunit;

namespace TodoApp.Tests;

public sealed class SmokeTests
{
    [Fact]
    public async Task Host_起動する_未定義パスは404()
    {
        await using var app = await TestWebApp.CreateAsync();
        using var client = app.CreateClient();
        using var response = await client.GetAsync(new Uri("/", UriKind.Relative), TestContext.Current.CancellationToken);
        Assert.Equal(System.Net.HttpStatusCode.NotFound, response.StatusCode);
    }
}
