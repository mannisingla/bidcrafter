using System.Text;
using System.Text.Json;
using System.Threading.RateLimiting;
using BidCraft.Api.Data;
using BidCraft.Api.Services;
using Microsoft.AspNetCore.Authentication.JwtBearer;
using Microsoft.AspNetCore.HttpOverrides;
using Microsoft.AspNetCore.Http.Features;
using Microsoft.AspNetCore.RateLimiting;
using Microsoft.EntityFrameworkCore;
using Microsoft.IdentityModel.Tokens;

var builder = WebApplication.CreateBuilder(args);
var config = builder.Configuration;

var jwtKey = config["Jwt:Key"];
if (string.IsNullOrWhiteSpace(jwtKey) || jwtKey.Length < 32)
    throw new InvalidOperationException("Jwt:Key must be set to at least 32 characters (see bidcraft.env)");

builder.Services.AddDbContext<AppDb>(o => o
    .UseNpgsql(config.GetConnectionString("Default"))
    .UseSnakeCaseNamingConvention());
builder.Services.AddSingleton<FileStore>();
builder.Services.AddSingleton<TokenService>();
builder.Services.AddScoped<JobQueue>();
builder.Services.AddScoped<Audit>();
builder.Services.AddHttpClient<AiClient>(c =>
{
    c.BaseAddress = new Uri(config["Ai:BaseUrl"] ?? "http://127.0.0.1:8001");
    c.DefaultRequestHeaders.Add("X-Internal-Token", config["Ai:InternalToken"] ?? "");
    c.Timeout = TimeSpan.FromSeconds(60);
});

builder.Services.AddAuthentication(JwtBearerDefaults.AuthenticationScheme).AddJwtBearer(o =>
{
    o.TokenValidationParameters = new TokenValidationParameters
    {
        ValidateIssuer = true,
        ValidIssuer = "bidcraft",
        ValidateAudience = true,
        ValidAudience = "bidcraft",
        ValidateIssuerSigningKey = true,
        IssuerSigningKey = new SymmetricSecurityKey(Encoding.UTF8.GetBytes(jwtKey)),
        ClockSkew = TimeSpan.FromMinutes(1),
    };
});
builder.Services.AddAuthorizationBuilder()
    .AddPolicy("Reviewer", p => p.RequireRole(Roles.Admin, Roles.Reviewer))
    .AddPolicy("Admin", p => p.RequireRole(Roles.Admin));

builder.Services.AddControllers().AddJsonOptions(o =>
    o.JsonSerializerOptions.PropertyNamingPolicy = JsonNamingPolicy.CamelCase);
// Uploads of up to 300 MB are allowed only where an action says so ([RequestSizeLimit] on the upload endpoints);
// every other request keeps Kestrel's 30 MB default (the sign-in endpoint is anonymous).
builder.Services.Configure<FormOptions>(o => o.MultipartBodyLengthLimit = 300L * 1024 * 1024);
// Sign-in attempts: 10 a minute per address (behind nginx the address comes from X-Forwarded-For).
builder.Services.AddRateLimiter(o =>
{
    o.RejectionStatusCode = StatusCodes.Status429TooManyRequests;
    o.AddPolicy("login", ctx => RateLimitPartition.GetFixedWindowLimiter(
        ctx.Connection.RemoteIpAddress?.ToString() ?? "unknown",
        _ => new FixedWindowRateLimiterOptions { PermitLimit = 10, Window = TimeSpan.FromMinutes(1), QueueLimit = 0 }));
});
builder.Services.Configure<ForwardedHeadersOptions>(o =>
    o.ForwardedHeaders = ForwardedHeaders.XForwardedFor | ForwardedHeaders.XForwardedProto);

var app = builder.Build();
app.UseForwardedHeaders();
app.UseRateLimiter();
app.UseAuthentication();
app.UseAuthorization();
app.MapControllers();
app.MapGet("/api/health", () => Results.Ok(new { status = "ok" }));

await Seed.EnsureAdminAsync(app.Services, config);
app.Run();
