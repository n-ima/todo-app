using Xunit;
using Microsoft.AspNetCore.Mvc.Testing;

namespace TodoApp.Tests;

public sealed class SmokeTests(WebApplicationFactory<Program> factory) : IClassFixture<WebApplicationFactory<Program>>
{
    [Fact]
    public async Task Host_起動する_未定義パスは404()
    {
        using var client = factory.CreateClient();
        using var response = await client.GetAsync(new Uri("/", UriKind.Relative), TestContext.Current.CancellationToken);
        Assert.Equal(System.Net.HttpStatusCode.NotFound, response.StatusCode);
    }
}
