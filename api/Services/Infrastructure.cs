using System.IdentityModel.Tokens.Jwt;
using System.Security.Claims;
using System.Text;
using System.Text.Json;
using BidCraft.Api.Data;
using Microsoft.EntityFrameworkCore;
using Microsoft.IdentityModel.Tokens;

namespace BidCraft.Api.Services;

/// <summary>Stores uploads under Files:Root. Paths saved in the database are relative to it.</summary>
public class FileStore(IConfiguration config)
{
    private readonly string _root = Path.GetFullPath(config["Files:Root"] ?? "/srv/bidcraft/files");

    public static readonly HashSet<string> AllowedExtensions =
        [".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".doc", ".docx", ".odt", ".xls", ".xlsx"];

    public async Task<string> SaveAsync(IFormFile file, string area, CancellationToken ct)
    {
        var ext = Path.GetExtension(file.FileName).ToLowerInvariant();
        if (!AllowedExtensions.Contains(ext))
            throw new InvalidOperationException($"File type {ext} is not supported");
        var relative = Path.Combine(area, DateTime.UtcNow.ToString("yyyy"), DateTime.UtcNow.ToString("MM"),
            $"{Guid.NewGuid():N}{ext}");
        var full = Resolve(relative);
        Directory.CreateDirectory(Path.GetDirectoryName(full)!);
        await using var stream = File.Create(full);
        await file.CopyToAsync(stream, ct);
        return relative;
    }

    public string Resolve(string relative)
    {
        var full = Path.GetFullPath(Path.Combine(_root, relative));
        if (!full.StartsWith(_root + Path.DirectorySeparatorChar, StringComparison.Ordinal))
            throw new InvalidOperationException("Path escapes the file store");
        return full;
    }

    public void Delete(string? relative)
    {
        if (string.IsNullOrEmpty(relative)) return;
        var full = Resolve(relative);
        if (File.Exists(full)) File.Delete(full);
    }
}

/// <summary>Adds work for the Python worker (it polls the jobs table).</summary>
public class JobQueue(AppDb db)
{
    public async Task<Job> EnqueueAsync(string type, object payload, Guid? userId, CancellationToken ct)
    {
        var job = new Job
        {
            Type = type,
            Payload = JsonSerializer.Serialize(payload),
            Status = "queued",
            CreatedBy = userId,
            CreatedAt = DateTime.UtcNow,
        };
        db.Jobs.Add(job);
        await db.SaveChangesAsync(ct);
        return job;
    }
}

public class Audit(AppDb db)
{
    public void Log(string actor, string action, string? entity = null, object? entityId = null, object? detail = null)
    {
        db.AuditLog.Add(new AuditEntry
        {
            At = DateTime.UtcNow,
            Actor = actor,
            Action = action,
            Entity = entity,
            EntityId = entityId?.ToString(),
            Detail = JsonSerializer.Serialize(detail ?? new { }),
        });
    }
}

public class TokenService(IConfiguration config)
{
    public string Issue(User user)
    {
        var key = new SymmetricSecurityKey(Encoding.UTF8.GetBytes(config["Jwt:Key"]!));
        var claims = new[]
        {
            new Claim(JwtRegisteredClaimNames.Sub, user.Id.ToString()),
            new Claim(JwtRegisteredClaimNames.Email, user.Email),
            new Claim("name", user.FullName),
            new Claim(ClaimTypes.Role, user.Role),
        };
        var token = new JwtSecurityToken(
            issuer: "bidcraft", audience: "bidcraft", claims: claims,
            expires: DateTime.UtcNow.AddHours(config.GetValue("Jwt:Hours", 10)),
            signingCredentials: new SigningCredentials(key, SecurityAlgorithms.HmacSha256));
        return new JwtSecurityTokenHandler().WriteToken(token);
    }
}

/// <summary>Calls the internal Python service (127.0.0.1 only).</summary>
public class AiClient(HttpClient http)
{
    public async Task<JsonElement> PostAsync(string path, object body, CancellationToken ct)
    {
        using var response = await http.PostAsJsonAsync(path, body, ct);
        response.EnsureSuccessStatusCode();
        return await response.Content.ReadFromJsonAsync<JsonElement>(cancellationToken: ct);
    }

    /// <summary>As PostAsync, but a refusal (4xx) comes back with its message (FastAPI's "detail") instead of throwing.</summary>
    public async Task<(bool Ok, JsonElement Body, string? Message)> TryPostAsync(string path, object body, CancellationToken ct)
    {
        using var response = await http.PostAsJsonAsync(path, body, ct);
        if ((int)response.StatusCode is >= 400 and < 500)
        {
            var error = await response.Content.ReadFromJsonAsync<JsonElement>(cancellationToken: ct);
            return (false, error, error.TryGetProperty("detail", out var d) && d.ValueKind == JsonValueKind.String ? d.GetString() : "Refused");
        }
        response.EnsureSuccessStatusCode();
        return (true, await response.Content.ReadFromJsonAsync<JsonElement>(cancellationToken: ct), null);
    }
}

public static class Seed
{
    /// <summary>Creates the first admin from Admin:Email / Admin:Password when no users exist.</summary>
    public static async Task EnsureAdminAsync(IServiceProvider services, IConfiguration config)
    {
        using var scope = services.CreateScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDb>();
        if (await db.Users.AnyAsync()) return;
        var email = config["Admin:Email"];
        var password = config["Admin:Password"];
        if (string.IsNullOrWhiteSpace(email) || string.IsNullOrWhiteSpace(password)) return;
        db.Users.Add(new User
        {
            Email = email.Trim().ToLowerInvariant(),
            FullName = config["Admin:Name"] ?? "Administrator",
            PasswordHash = BCrypt.Net.BCrypt.HashPassword(password),
            Role = Roles.Admin,
            CreatedAt = DateTime.UtcNow,
        });
        await db.SaveChangesAsync();
    }
}

public static class UserExtensions
{
    public static Guid? UserId(this ClaimsPrincipal user)
    {
        var raw = user.FindFirstValue(ClaimTypes.NameIdentifier) ?? user.FindFirstValue(JwtRegisteredClaimNames.Sub);
        return Guid.TryParse(raw, out var id) ? id : null;
    }

    public static string Actor(this ClaimsPrincipal user) =>
        user.FindFirstValue(ClaimTypes.Email) ?? user.FindFirstValue(JwtRegisteredClaimNames.Email) ?? "unknown";
}
