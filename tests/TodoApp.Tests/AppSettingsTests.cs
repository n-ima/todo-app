using Microsoft.Extensions.Configuration;
using Xunit;

namespace TodoApp.Tests;

// レビュー BLOCKER H1: 不正な JSON エスケープは本番起動時まで露見しないため、全構成ファイルの読込を検証する。
public class AppSettingsTests
{
    private static string AppDir()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !File.Exists(Path.Combine(dir.FullName, "TodoApp.sln")))
            dir = dir.Parent;
        Assert.NotNull(dir);
        return Path.Combine(dir!.FullName, "src", "TodoApp");
    }

    [Fact]
    public void AllAppSettingsFiles_LoadWithoutError()
    {
        var files = Directory.GetFiles(AppDir(), "appsettings*.json");
        Assert.True(files.Length >= 3);
        foreach (var f in files)
            new ConfigurationBuilder().AddJsonFile(f, optional: false, reloadOnChange: false).Build();
    }

    [Fact]
    public void Production_CertificatePath_IsWindowsPath()
    {
        var config = new ConfigurationBuilder()
            .AddJsonFile(Path.Combine(AppDir(), "appsettings.Production.json"), optional: false, reloadOnChange: false)
            .Build();
        Assert.Equal(@"C:\TodoApp\certs\server.pfx", config["Kestrel:Endpoints:Https:Certificate:Path"]);
    }
}
