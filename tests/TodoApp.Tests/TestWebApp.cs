using System.Net;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Data.Sqlite;
using Microsoft.Extensions.DependencyInjection;
using TodoApp.Cli;
using Xunit;

namespace TodoApp.Tests;

/// <summary>
/// 一時フォルダに版の合った DB を作り Paths:Data を向けた WebApplicationFactory（起動時の DB 版チェックのため。DD-02 §1）。
/// 要求元 IP はヘッダー X-Test-Remote-IP で指定する（既定はループバック）。パスが /test/throw で終わる要求は想定外の例外を投げる。
/// </summary>
public sealed class TestWebApp : IAsyncDisposable
{
    public const string RemoteIpHeader = "X-Test-Remote-IP";
    public const string ThrowPath = "/test/throw";
    public const string ThrowMarker = "SECRET-STACK-MARKER";

    private readonly WebApplicationFactory<Program> _factory;
    private bool _stopped;

    private TestWebApp(string dataDirectory, WebApplicationFactory<Program> factory)
    {
        DataDirectory = dataDirectory;
        _factory = factory;
    }

    public string DataDirectory { get; }

    public static async Task<TestWebApp> CreateAsync()
    {
        var dir = Path.Combine(Path.GetTempPath(), "todoapp-web-" + Guid.NewGuid().ToString("N"));
        Assert.Equal(0, await CliRunner.RunAsync(["migrate", "--init", "--data", dir], TextWriter.Null, TextWriter.Null));
        var factory = new WebApplicationFactory<Program>().WithWebHostBuilder(b =>
        {
            b.UseSetting("Paths:Data", dir);
            b.ConfigureServices(s => s.AddSingleton<IStartupFilter, TestStartupFilter>());
        });
        return new TestWebApp(dir, factory);
    }

    public HttpClient CreateClient(bool https = true) => _factory.CreateClient(new WebApplicationFactoryClientOptions
    {
        BaseAddress = new Uri(https ? "https://localhost" : "http://localhost"),
        AllowAutoRedirect = false,
    });

    /// <summary>ホストを止めてログを書き切る（一時フォルダは残す）。</summary>
    public async Task StopAsync()
    {
        if (!_stopped)
        {
            _stopped = true;
            await _factory.DisposeAsync();
        }
    }

    public async ValueTask DisposeAsync()
    {
        // ログファイルを閉じてから一時フォルダを消す
        await StopAsync();
        SqliteConnection.ClearAllPools();
        if (Directory.Exists(DataDirectory))
        {
            Directory.Delete(DataDirectory, recursive: true);
        }
    }

    private sealed class TestStartupFilter : IStartupFilter
    {
        public Action<IApplicationBuilder> Configure(Action<IApplicationBuilder> next) => app =>
        {
            app.Use((context, nextMiddleware) =>
            {
                var header = context.Request.Headers[RemoteIpHeader].ToString();
                context.Connection.RemoteIpAddress = header.Length > 0 ? IPAddress.Parse(header) : IPAddress.Loopback;
                return nextMiddleware(context);
            });
            next(app);
            // アプリのパイプラインで一致しなかった要求がここへ来る（例外ハンドラの内側）
            app.Use((HttpContext context, RequestDelegate nextMiddleware) =>
                context.Request.Path.Value?.EndsWith(ThrowPath, StringComparison.Ordinal) == true
                    ? throw new InvalidOperationException("テスト用の想定外例外 " + ThrowMarker)
                    : nextMiddleware(context));
        };
    }
}
