using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Nodes;
using BidCraft.Api.Data;
using BidCraft.Api.Services;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;

namespace BidCraft.Api.Controllers;

[ApiController]
[Route("api/library")]
[Authorize]
public class LibraryController(AppDb db, FileStore files, JobQueue jobs, Audit audit, AiClient ai) : ControllerBase
{
    public record UpdateDocument(string Title, string Category, string DocType, string Sensitivity,
        DateOnly? ValidUntil, JsonElement Metadata);

    private object Summary(LibraryDocument d) => new
    {
        d.Id, d.Category, d.DocType, d.Title, d.OriginalName, d.MimeType, d.PageCount, d.Status, d.Error,
        Metadata = JsonDocument.Parse(d.Metadata).RootElement, d.ValidUntil, d.Sensitivity, d.CreatedAt, d.UpdatedAt,
        FileSize = SizeOf(OriginalPdf(d) ?? d.FilePath),
    };

    /// <summary>The document as uploaded, as a PDF: the file itself, or the plain conversion of an image or Word file.
    /// An OCR'd copy (the PdfPath of older scans) is kept only for its text: what is viewed, downloaded and enclosed
    /// in a bid is the original (as ai-service pdf/assembler.py _original_pdf).</summary>
    private string? OriginalPdf(LibraryDocument d)
    {
        if (d.FilePath.EndsWith(".pdf", StringComparison.OrdinalIgnoreCase)) return d.FilePath;
        var converted = Path.ChangeExtension(d.FilePath, ".converted.pdf");
        return System.IO.File.Exists(files.Resolve(converted)) ? converted : d.PdfPath;
    }

    /// <summary>The bid a document was uploaded for, or null for a library document.</summary>
    internal static string? BidOf(LibraryDocument d) =>
        JsonNode.Parse(string.IsNullOrEmpty(d.Metadata) ? "{}" : d.Metadata)?["bid_id"] is JsonValue v && v.TryGetValue<string>(out var s) ? s : null;

    /// <summary>Size in bytes of the PDF (or of the uploaded file until it has been converted).</summary>
    private long? SizeOf(string relative)
    {
        try { var f = new FileInfo(files.Resolve(relative)); return f.Exists ? f.Length : null; }
        catch (InvalidOperationException) { return null; }
    }

    /// <summary>The library, or with <paramref name="bid"/> the documents uploaded for that bid only (its EMD, power
    /// of attorney, signed forms…). A bid's documents are not part of the library: they are kept in
    /// metadata.bid_id, enclosed only in that bid and not used by the eligibility check.</summary>
    [HttpGet]
    public async Task<IActionResult> List([FromQuery] string? category, [FromQuery] Guid? bid, CancellationToken ct)
    {
        var q = (bid is Guid b
            ? db.LibraryDocuments.FromSqlInterpolated($"SELECT * FROM library_documents WHERE metadata->>'bid_id' = {b.ToString()}")
            : db.LibraryDocuments.FromSqlRaw("SELECT * FROM library_documents WHERE metadata->>'bid_id' IS NULL")).AsNoTracking();
        if (!string.IsNullOrEmpty(category)) q = q.Where(d => d.Category == category);
        var docs = await q.OrderByDescending(d => d.CreatedAt).ToListAsync(ct);
        return Ok(docs.Select(Summary));
    }

    [HttpGet("{id:guid}")]
    public async Task<IActionResult> Get(Guid id, CancellationToken ct)
    {
        var d = await db.LibraryDocuments.AsNoTracking().SingleOrDefaultAsync(x => x.Id == id, ct);
        return d is null ? NotFound() : Ok(Summary(d));
    }

    [HttpPost]
    [RequestSizeLimit(300L * 1024 * 1024)]
    public async Task<IActionResult> Upload([FromForm] List<IFormFile> files, [FromForm] string category,
        [FromForm] string? sensitivity, [FromForm] string? docType, [FromForm] Guid? parentId, [FromForm] string? title,
        [FromForm] Guid? bidId, CancellationToken ct)
    {
        if (bidId is not null)          // a document for one bid only (EMD, power of attorney, signed forms…)
        {
            if (parentId is not null) return BadRequest(new { message = "A bid's document cannot belong to a project or candidate" });
            if (!await db.Bids.AnyAsync(b => b.Id == bidId, ct)) return BadRequest(new { message = "The bid no longer exists" });
            category = "uploads";
        }
        if (!LibraryDocument.Categories.Contains(category)) return BadRequest(new { message = "Unknown category" });
        sensitivity ??= "internal";
        if (!LibraryDocument.Sensitivities.Contains(sensitivity)) return BadRequest(new { message = "Unknown sensitivity" });
        if (files.Count == 0) return BadRequest(new { message = "Choose at least one file" });
        if (docType is { Length: > 40 }) return BadRequest(new { message = "Unknown document type" });
        if (title is { Length: > 200 }) return BadRequest(new { message = "The title is too long" });
        // Related documents point to their parent: an Experience project's certificates, evidence and RFP by
        // metadata.work_order_id (as completion certificates always have), a candidate's certificates by cv_id.
        var metadata = "{}";
        if (parentId is not null)
        {
            var parent = await db.LibraryDocuments.AsNoTracking().SingleOrDefaultAsync(x => x.Id == parentId, ct);
            if (parent is null) return BadRequest(new { message = "The project or candidate no longer exists" });
            category = parent.Category;
            metadata = JsonSerializer.Serialize(new Dictionary<string, string> { [ParentKey(parent.Category)] = parent.Id.ToString() });
        }
        if (bidId is not null) metadata = JsonSerializer.Serialize(new Dictionary<string, string> { ["bid_id"] = bidId.Value.ToString() });

        foreach (var f in files)       // check every file first, so a bad one does not leave the others half-added
        {
            var ext = Path.GetExtension(f.FileName).ToLowerInvariant();
            if (!FileStore.AllowedExtensions.Contains(ext)) return BadRequest(new { message = $"File type {ext} is not supported ({f.FileName})" });
        }
        var created = new List<object>();
        var duplicates = new List<object>();
        foreach (var file in files)
        {
            // The same file twice adds nothing: report the copy already there instead. For a bid, "there" is the
            // library or this bid's own documents (another bid's copy belongs to that bid).
            string sha;
            await using (var s = file.OpenReadStream()) sha = Convert.ToHexString(await SHA256.HashDataAsync(s, ct)).ToLowerInvariant();
            var copies = await db.LibraryDocuments.AsNoTracking().Where(x => x.FileSha256 == sha).ToListAsync(ct);
            var existing = copies.FirstOrDefault(x => BidOf(x) is null || (bidId is not null && BidOf(x) == bidId.ToString()));
            if (existing is not null)
            {
                duplicates.Add(new { FileName = file.FileName, Existing = Summary(existing) });
                continue;
            }

            string path;
            try { path = await SaveUploadAsync(file, ct); }
            catch (InvalidOperationException ex) { return BadRequest(new { message = ex.Message }); }

            var doc = new LibraryDocument
            {
                Category = category,
                DocType = string.IsNullOrWhiteSpace(docType) ? "other" : docType.Trim(),
                Metadata = metadata,
                Title = files.Count == 1 && !string.IsNullOrWhiteSpace(title) ? title.Trim() : Path.GetFileNameWithoutExtension(file.FileName),
                FilePath = path,
                FileSha256 = sha,
                OriginalName = file.FileName,
                MimeType = file.ContentType ?? "application/octet-stream",
                Sensitivity = sensitivity,
                CreatedBy = User.UserId(),
                CreatedAt = DateTime.UtcNow,
                UpdatedAt = DateTime.UtcNow,
            };
            db.LibraryDocuments.Add(doc);
            await db.SaveChangesAsync(ct);
            await jobs.EnqueueAsync("ingest_document", new { document_id = doc.Id }, User.UserId(), ct);
            audit.Log(User.Actor(), "document.uploaded", "document", doc.Id, new { doc.Category, doc.OriginalName });
            created.Add(Summary(doc));
        }
        await db.SaveChangesAsync(ct);
        return Ok(new { created, duplicates });
    }

    // The form field is also named "files", so the file store is reached through this helper.
    private Task<string> SaveUploadAsync(IFormFile f, CancellationToken ct) => files.SaveAsync(f, "library", ct);

    [HttpPut("{id:guid}")]
    public async Task<IActionResult> Update(Guid id, UpdateDocument req, CancellationToken ct)
    {
        var d = await db.LibraryDocuments.FindAsync([id], ct);
        if (d is null) return NotFound();
        if (!LibraryDocument.Categories.Contains(req.Category) || !LibraryDocument.Sensitivities.Contains(req.Sensitivity))
            return BadRequest(new { message = "Unknown category or sensitivity" });
        if (req.Metadata.ValueKind != JsonValueKind.Object) return BadRequest(new { message = "Details must be a JSON object" });
        d.Title = req.Title.Trim();
        d.Category = req.Category;
        d.DocType = string.IsNullOrWhiteSpace(req.DocType) ? "other" : req.DocType.Trim();
        d.Sensitivity = req.Sensitivity;
        d.ValidUntil = req.ValidUntil;
        var metadata = JsonNode.Parse(req.Metadata.GetRawText())!.AsObject();
        if (BidOf(d) is { } bid) metadata["bid_id"] = bid;         // a bid's document stays with its bid
        d.Metadata = metadata.ToJsonString();
        d.UpdatedAt = DateTime.UtcNow;
        audit.Log(User.Actor(), "document.updated", "document", d.Id, new { d.DocType, d.Sensitivity });
        await db.SaveChangesAsync(ct);
        return Ok(Summary(d));
    }

    [HttpPost("{id:guid}/reprocess")]
    public async Task<IActionResult> Reprocess(Guid id, CancellationToken ct)
    {
        var d = await db.LibraryDocuments.FindAsync([id], ct);
        if (d is null) return NotFound();
        d.Status = "uploaded";
        await db.SaveChangesAsync(ct);
        await jobs.EnqueueAsync("ingest_document", new { document_id = id }, User.UserId(), ct);
        return Accepted();
    }

    [HttpDelete("{id:guid}")]
    public async Task<IActionResult> Delete(Guid id, CancellationToken ct)
    {
        var d = await db.LibraryDocuments.FindAsync([id], ct);
        if (d is null) return NotFound();
        // An Experience project takes its related documents (certificates, evidence, RFP) with it, a candidate
        // their certificates.
        var related = await db.LibraryDocuments
            .FromSqlInterpolated($"SELECT * FROM library_documents WHERE metadata->>'work_order_id' = {id.ToString()} OR metadata->>'cv_id' = {id.ToString()}")
            .ToListAsync(ct);
        db.LibraryDocuments.RemoveRange(related);
        db.LibraryDocuments.Remove(d);
        audit.Log(User.Actor(), "document.deleted", "document", id, new { d.Title, related = related.Count });
        await db.SaveChangesAsync(ct);
        foreach (var x in related.Append(d))
            foreach (var path in new[] { x.FilePath, x.PdfPath, OriginalPdf(x) }.Distinct())    // upload, OCR copy, conversion
                files.Delete(path);
        return NoContent();
    }

    /// <summary>Fill details again: an Experience project's from its purchase order, completion certificate or RFP
    /// (ai-service ingest/project.py), a candidate's from their CV (ingest/cv.py).</summary>
    [HttpPost("{id:guid}/extract")]
    public async Task<IActionResult> Extract(Guid id, CancellationToken ct)
    {
        var d = await db.LibraryDocuments.FindAsync([id], ct);
        if (d is null) return NotFound();
        var meta = System.Text.Json.Nodes.JsonNode.Parse(d.Metadata)!.AsObject();
        var linked = meta["work_order_id"] is System.Text.Json.Nodes.JsonValue link && link.TryGetValue<string>(out var to) && to.Length > 0;
        var job = d.Category == "experience" && (d.DocType == "work_order" ? !linked
                : (d.DocType is "completion_certificate" or "project_rfp") && linked) ? "extract_project"
            : d.Category == "hr" && d.DocType == "cv" ? "extract_cv" : null;
        if (job is null || d.Status != "ready")
            return BadRequest(new { message = "Details can be read from a project's purchase order, completion certificate or RFP, or from a CV, once it has been read" });
        meta["extraction"] = new System.Text.Json.Nodes.JsonObject { ["status"] = "queued" };
        d.Metadata = meta.ToJsonString();
        d.UpdatedAt = DateTime.UtcNow;
        await db.SaveChangesAsync(ct);
        await jobs.EnqueueAsync(job, new { document_id = id }, User.UserId(), ct);
        return Accepted();
    }

    /// <summary>Metadata key a related document uses to point to its parent.</summary>
    private static string ParentKey(string category) => category == "hr" ? "cv_id" : "work_order_id";

    /// <summary>Original file, or its PDF rendition with ?pdf=true.</summary>
    [HttpGet("{id:guid}/file")]
    public async Task<IActionResult> Download(Guid id, [FromQuery] bool pdf, CancellationToken ct)
    {
        var d = await db.LibraryDocuments.AsNoTracking().SingleOrDefaultAsync(x => x.Id == id, ct);
        if (d is null) return NotFound();
        if (pdf && OriginalPdf(d) is { } original) return PhysicalFile(files.Resolve(original), "application/pdf");
        return PhysicalFile(files.Resolve(d.FilePath), d.MimeType, d.OriginalName);
    }

    /// <summary>Extracted text, used to insert bid literature into a section.</summary>
    [HttpGet("{id:guid}/text")]
    public async Task<IActionResult> Text(Guid id, CancellationToken ct)
    {
        var d = await db.LibraryDocuments.AsNoTracking().SingleOrDefaultAsync(x => x.Id == id, ct);
        if (d is null) return NotFound();
        var text = d.ExtractedText ?? "";
        return Ok(new { d.Title, Text = text.Length > 40000 ? text[..40000] : text });
    }

    /// <summary>Local semantic search over the library (runs on this server).</summary>
    [HttpGet("search")]
    public async Task<IActionResult> Search([FromQuery] string q, [FromQuery] string? category, CancellationToken ct)
    {
        if (string.IsNullOrWhiteSpace(q) || q.Length < 2) return BadRequest(new { message = "Type at least two characters" });
        var result = await ai.PostAsync("/search", new
        {
            query = q,
            categories = string.IsNullOrEmpty(category) ? null : new[] { category },
            k = 12,
        }, ct);
        return Ok(result);
    }
}
