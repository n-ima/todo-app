var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();
app.Run();

// WebApplicationFactory<Program> からテストで参照するため公開する
public partial class Program;
