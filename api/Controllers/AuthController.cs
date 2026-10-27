using BidCraft.Api.Data;
using BidCraft.Api.Services;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;

namespace BidCraft.Api.Controllers;

[ApiController]
[Route("api/auth")]
public class AuthController(AppDb db, TokenService tokens, Audit audit) : ControllerBase
{
    public record LoginRequest(string Email, string Password);

    [HttpPost("login")]
    [AllowAnonymous]
    [Microsoft.AspNetCore.RateLimiting.EnableRateLimiting("login")]
    public async Task<IActionResult> Login(LoginRequest req, CancellationToken ct)
    {
        var email = (req.Email ?? "").Trim().ToLowerInvariant();
        var user = await db.Users.SingleOrDefaultAsync(u => u.Email == email, ct);
        if (user is null || !BCrypt.Net.BCrypt.Verify(req.Password ?? "", user.PasswordHash))
        {
            audit.Log(email.Length > 120 ? email[..120] : email, "auth.failed");
            await db.SaveChangesAsync(ct);
            return Unauthorized(new { message = "Email or password is incorrect" });
        }
        audit.Log(user.Email, "auth.login", "user", user.Id);
        await db.SaveChangesAsync(ct);
        return Ok(new { token = tokens.Issue(user), user = new { user.Id, user.Email, user.FullName, user.Role } });
    }

    [HttpGet("me")]
    [Authorize]
    public async Task<IActionResult> Me(CancellationToken ct)
    {
        var id = User.UserId();
        var user = await db.Users.FindAsync([id], ct);
        return user is null ? Unauthorized() : Ok(new { user.Id, user.Email, user.FullName, user.Role });
    }
}

[ApiController]
[Route("api/users")]
[Authorize(Policy = "Admin")]
public class UsersController(AppDb db, Audit audit) : ControllerBase
{
    public record CreateUser(string Email, string FullName, string Password, string Role);

    [HttpGet]
    public async Task<IActionResult> List(CancellationToken ct) =>
        Ok(await db.Users.OrderBy(u => u.Email).Select(u => new { u.Id, u.Email, u.FullName, u.Role, u.CreatedAt }).ToListAsync(ct));

    [HttpPost]
    public async Task<IActionResult> Create(CreateUser req, CancellationToken ct)
    {
        if (!Roles.All.Contains(req.Role)) return BadRequest(new { message = "Unknown role" });
        if (string.IsNullOrWhiteSpace(req.Password) || req.Password.Length < 10)
            return BadRequest(new { message = "Password must be at least 10 characters" });
        var email = req.Email.Trim().ToLowerInvariant();
        if (await db.Users.AnyAsync(u => u.Email == email, ct)) return Conflict(new { message = "User already exists" });
        var user = new User
        {
            Email = email, FullName = req.FullName.Trim(), Role = req.Role,
            PasswordHash = BCrypt.Net.BCrypt.HashPassword(req.Password), CreatedAt = DateTime.UtcNow,
        };
        db.Users.Add(user);
        audit.Log(User.Actor(), "user.created", "user", user.Id, new { user.Email, user.Role });
        await db.SaveChangesAsync(ct);
        return Ok(new { user.Id });
    }
}

/// <summary>Company details for the letterhead, seal and cover in the web preview (from COMPANY_* settings).</summary>
[ApiController]
[Route("api/company")]
[Authorize]
public class CompanyController(IConfiguration config) : ControllerBase
{
    [HttpGet]
    public IActionResult Get() => Ok(new
    {
        Name = config["COMPANY_NAME"] ?? "Your Company Limited",
        City = config["COMPANY_CITY"] ?? "",
        Cin = config["COMPANY_CIN"] ?? "",
    });
}
