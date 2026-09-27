using System.Net;
using System.Text.RegularExpressions;
using Dapper;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Data.Sqlite;
using Microsoft.Extensions.DependencyInjection;
using TodoApp.Cli;
using TodoApp.Data;
using TodoApp.Services;
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
    public const string ApiDbWritePath = "/api/test/db-write";

    private readonly WebApplicationFactory<Program> _factory;
    private bool _stopped;

    private TestWebApp(string dataDirectory, WebApplicationFactory<Program> factory)
    {
        DataDirectory = dataDirectory;
        _factory = factory;
    }

    public string DataDirectory { get; }

    public static async Task<TestWebApp> CreateAsync(Action<IServiceCollection>? configureServices = null)
    {
        var dir = Path.Combine(Path.GetTempPath(), "todoapp-web-" + Guid.NewGuid().ToString("N"));
        Assert.Equal(0, await CliRunner.RunAsync(["migrate", "--init", "--data", dir], TextReader.Null, TextWriter.Null, TextWriter.Null));
        var factory = new WebApplicationFactory<Program>().WithWebHostBuilder(b =>
        {
            b.UseSetting("Paths:Data", dir);
            b.ConfigureServices(s =>
            {
                s.AddSingleton<IStartupFilter, TestStartupFilter>();
                configureServices?.Invoke(s);
            });
        });
        return new TestWebApp(dir, factory);
    }

    public HttpClient CreateClient(bool https = true) => _factory.CreateClient(new WebApplicationFactoryClientOptions
    {
        BaseAddress = new Uri(https ? "https://localhost" : "http://localhost"),
        AllowAutoRedirect = false,
    });

    public string DatabasePath => Path.Combine(DataDirectory, CliRunner.DatabaseFileName);

    /// <summary>テスト用の利用者を DB に直接作る（利用者管理の画面・CLI を経由しない）。戻り値は users.id。</summary>
    public async Task<long> CreateUserAsync(
        string loginId, string password, string role = "member", bool mustChangePassword = false, bool isActive = true, string? displayName = null)
    {
        await using var connection = new SqliteConnection(new SqliteConnectionStringBuilder { DataSource = DatabasePath }.ToString());
        await connection.OpenAsync();
        var now = UserRepository.FormatUtc(DateTimeOffset.UtcNow);
        return await connection.ExecuteScalarAsync<long>(
            "INSERT INTO users (login_id, display_name, role, password_hash, must_change_password, is_active, security_stamp, created_at, updated_at) " +
            "VALUES (@loginId, @displayName, @role, @hash, @mcp, @active, @stamp, @now, @now) RETURNING id",
            new
            {
                loginId,
                displayName = displayName ?? loginId + " さん",
                role,
                hash = AuthService.PasswordHasher.HashPassword(new UserRecord(), password),
                mcp = mustChangePassword ? 1 : 0,
                active = isActive ? 1 : 0,
                stamp = Guid.NewGuid().ToString("N"),
                now,
            });
    }

    /// <summary>DB に SQL を直接実行する（無効化・stamp 更新の再現用）。</summary>
    public async Task ExecuteAsync(string sql, object? param = null)
    {
        await using var connection = new SqliteConnection(new SqliteConnectionStringBuilder { DataSource = DatabasePath }.ToString());
        await connection.OpenAsync();
        await connection.ExecuteAsync(sql, param);
    }

    /// <summary>画面のレイアウトの meta から Antiforgery トークンを取る（Cookie はクライアントに保存される）。</summary>
    public static async Task<string> GetTokenAsync(HttpClient client, string path = "/login")
    {
        var body = await client.GetStringAsync(new Uri(path, UriKind.Relative));
        return Regex.Match(body, "<meta name=\"csrf-token\" content=\"([^\"]+)\"").Groups[1].Value;
    }

    /// <summary>フォームを Antiforgery トークン付きで POST する。</summary>
    public static async Task<HttpResponseMessage> PostFormAsync(HttpClient client, string path, IDictionary<string, string> fields, string tokenPath = "/login")
    {
        var values = new Dictionary<string, string>(fields) { ["__RequestVerificationToken"] = await GetTokenAsync(client, tokenPath) };
        using var content = new FormUrlEncodedContent(values);
        return await client.PostAsync(new Uri(path, UriKind.Relative), content);
    }

    public static Task<HttpResponseMessage> LoginAsync(HttpClient client, string loginId, string password, string? returnUrl = null)
    {
        var fields = new Dictionary<string, string> { ["loginId"] = loginId, ["password"] = password };
        if (returnUrl is not null)
        {
            fields["returnUrl"] = returnUrl;
        }

        return PostFormAsync(client, "/login", fields);
    }

    /// <summary>一般利用者を作ってログイン済みのクライアントを返す。</summary>
    public async Task<HttpClient> CreateLoggedInClientAsync(string loginId = "user1", string password = "password-1")
    {
        await CreateUserAsync(loginId, password);
        var client = CreateClient();
        using var response = await LoginAsync(client, loginId, password);
        Assert.Equal(HttpStatusCode.Redirect, response.StatusCode);
        return client;
    }

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
        // 閉じた直後のログファイルを Windows 側（ウイルス対策等）が一瞬掴むことがあるため、少し待って再試行する
        for (var attempt = 1; Directory.Exists(DataDirectory); attempt++)
        {
            try
            {
                Directory.Delete(DataDirectory, recursive: true);
            }
            catch (IOException) when (attempt < 20)
            {
                await Task.Delay(100);
            }
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
            // /api 配下で書き込みトランザクションを張るテスト用の口（本体に /api の書き込みエンドポイントがまだ無いため）
            app.Use(async (HttpContext context, RequestDelegate nextMiddleware) =>
            {
                if (context.Request.Path.Equals(ApiDbWritePath, StringComparison.Ordinal))
                {
                    var db = context.RequestServices.GetRequiredService<Db>();
                    await db.WriteAsync((_, _) => Task.FromResult(0));
                    await context.Response.WriteAsync("OK");
                    return;
                }

                await nextMiddleware(context);
            });
        };
    }
}

/// <summary>
/// Web ホストを立てるテストクラスを直列にする（Serilog の静的ロガーはプロセスで 1 つのため、並列に立てるとログファイルが混ざる）。
/// </summary>
[CollectionDefinition(Name, DisableParallelization = true)]
public sealed class WebHostSerial
{
    public const string Name = "WebHost";
}
