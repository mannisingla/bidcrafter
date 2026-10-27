using BidCraft.Api.Data;
using BidCraft.Api.Services;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;

namespace BidCraft.Api.Controllers;

/// <summary>Signature and stamp images, uploaded once and chosen for each bid on the Preview &amp; sign screen
/// (bid settings sign.image_id and sign.stamp_id; the PDF is stamped with them in ai-service pdf/assembler.py).</summary>
[ApiController]
[Route("api/sign-images")]
[Authorize]
public class SignImagesController(AppDb db, FileStore store, Audit audit) : ControllerBase
{
    private static readonly HashSet<string> Kinds = ["signature", "stamp"];
    private const long MaxBytes = 5 * 1024 * 1024;

    [HttpGet]
    public async Task<IActionResult> List(CancellationToken ct) =>
        Ok(await db.SignImages.AsNoTracking().OrderByDescending(x => x.CreatedAt)
            .Select(x => new { x.Id, x.Kind, x.Name, x.CreatedAt }).ToListAsync(ct));

    [HttpPost]
    [RequestSizeLimit(MaxBytes + 64 * 1024)]
    public async Task<IActionResult> Upload([FromForm] IFormFile file, [FromForm] string kind, [FromForm] string? name, CancellationToken ct)
    {
        if (!Kinds.Contains(kind)) return BadRequest(new { message = "Choose signature or stamp" });
        var ext = Path.GetExtension(file.FileName).ToLowerInvariant();
        if (ext is not (".png" or ".jpg" or ".jpeg")) return BadRequest(new { message = "Use a PNG or JPG image" });
        if (file.Length > MaxBytes) return BadRequest(new { message = "The image is larger than 5 MB" });
        if (!await IsImageAsync(file, ct)) return BadRequest(new { message = "The file is not a PNG or JPG image" });
        var label = string.IsNullOrWhiteSpace(name) ? Path.GetFileNameWithoutExtension(file.FileName) : name.Trim();
        var image = new SignImage
        {
            Id = Guid.NewGuid(),
            Kind = kind,
            Name = label.Length > 120 ? label[..120] : label,
            FilePath = await store.SaveAsync(file, "signatures", ct),
            CreatedBy = User.UserId(),
            CreatedAt = DateTime.UtcNow,
        };
        db.SignImages.Add(image);
        audit.Log(User.Actor(), "sign_image.uploaded", "sign_image", image.Id, new { image.Kind, image.Name });
        await db.SaveChangesAsync(ct);
        return Ok(new { image.Id, image.Kind, image.Name, image.CreatedAt });
    }

    [HttpGet("{id:guid}/file")]
    public async Task<IActionResult> Image(Guid id, CancellationToken ct)
    {
        var x = await db.SignImages.AsNoTracking().SingleOrDefaultAsync(i => i.Id == id, ct);
        if (x is null) return NotFound();
        return PhysicalFile(store.Resolve(x.FilePath), x.FilePath.EndsWith(".png", StringComparison.OrdinalIgnoreCase) ? "image/png" : "image/jpeg");
    }

    /// <summary>Bids that use the image are then built without it until another is chosen.</summary>
    [HttpDelete("{id:guid}")]
    public async Task<IActionResult> Delete(Guid id, CancellationToken ct)
    {
        var x = await db.SignImages.FindAsync([id], ct);
        if (x is null) return NotFound();
        db.SignImages.Remove(x);
        audit.Log(User.Actor(), "sign_image.deleted", "sign_image", id, new { x.Kind, x.Name });
        await db.SaveChangesAsync(ct);
        store.Delete(x.FilePath);
        return NoContent();
    }

    /// <summary>PNG or JPEG by its first bytes, not by its name.</summary>
    private static async Task<bool> IsImageAsync(IFormFile file, CancellationToken ct)
    {
        var head = new byte[8];
        await using var s = file.OpenReadStream();
        var n = await s.ReadAsync(head, ct);
        return n >= 4 && (head[0] == 0x89 && head[1] == 0x50 && head[2] == 0x4E && head[3] == 0x47
                          || head[0] == 0xFF && head[1] == 0xD8 && head[2] == 0xFF);
    }
}
