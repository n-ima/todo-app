using TodoApp.Infrastructure;

var builder = WebApplication.CreateBuilder(args);
AppConfiguration.AddLocalSettings(builder.Configuration);
AppConfiguration.AddAppServices(builder.Services, builder.Configuration);
var app = builder.Build();
app.Run();

// WebApplicationFactory<Program> からテストで参照するため公開する
public partial class Program;
