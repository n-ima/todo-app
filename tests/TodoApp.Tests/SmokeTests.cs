using Xunit;

namespace TodoApp.Tests;

[Collection(WebHostSerial.Name)]
public sealed class SmokeTests
{
    [Fact]
    public async Task Host_起動する_未定義パスは404()
    {
        await using var app = await TestWebApp.CreateAsync();
        // 既定の認可で未ログインはログインへ転送されるため、ログインしてから確かめる
        using var client = await app.CreateLoggedInClientAsync();
        using var response = await client.GetAsync(new Uri("/no-such-page", UriKind.Relative), TestContext.Current.CancellationToken);
        Assert.Equal(System.Net.HttpStatusCode.NotFound, response.StatusCode);
    }
}
