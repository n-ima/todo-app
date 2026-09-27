using TodoApp.Cli;
using TodoApp.Infrastructure;

if (args.Length > 0 && CliRunner.IsSubcommand(args[0]))
{
    return await CliRunner.RunAsync(args, Console.In, Console.Out, Console.Error);
}

return await WebHostRunner.RunAsync(args);

// WebApplicationFactory<Program> からテストで参照するため公開する
public partial class Program;
