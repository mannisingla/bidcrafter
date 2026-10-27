using System.Text.Json;
using System.Text.Json.Nodes;
using BidCraft.Api.Data;
using BidCraft.Api.Services;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;

namespace BidCraft.Api.Controllers;

[ApiController]
[Route("api/bids")]
[Authorize]
public class BidsController(AppDb db, FileStore store, JobQueue jobs, Audit audit, AiClient ai) : ControllerBase
{
    public record SectionUpdate(string Title, string Html, bool Include);
    public record NewSection(string Kind, string Title, Guid? AfterId);
    public record Reorder(List<Guid> Ids);
    public record SectionDocuments(List<Guid> DocumentIds);
    public record DraftRequest(bool Overwrite);

    private static JsonElement Json(string raw) => JsonDocument.Parse(string.IsNullOrEmpty(raw) ? "{}" : raw).RootElement;

    /// <summary>Documents a bid may enclose: the library, and the documents uploaded for this bid (not another bid's).</summary>
    private IQueryable<LibraryDocument> Attachable(Guid bidId) => db.LibraryDocuments.FromSqlInterpolated(
        $"SELECT * FROM library_documents WHERE metadata->>'bid_id' IS NULL OR metadata->>'bid_id' = {bidId.ToString()}");

    private static object Summary(Bid b) => new
    {
        b.Id, b.Title, b.RfpRef, b.Department, b.DueAt, b.Status, b.Error, b.OutputPageCount,
        HasPdf = b.OutputPdfPath != null, b.CreatedAt, b.UpdatedAt,
    };

    [HttpGet]
    public async Task<IActionResult> List(CancellationToken ct) =>
        Ok((await db.Bids.AsNoTracking().OrderByDescending(b => b.CreatedAt).ToListAsync(ct)).Select(Summary));

    /// <summary>Kinds of document one tender can have; corrigenda, addenda and clarifications amend the others.</summary>
    private static readonly string[] DocumentKinds = ["rfp", "corrigendum", "addendum", "clarification", "annexure", "other"];

    /// <summary>Facts of the tender a person may correct on the RFP analysis screen (ai-service rfp/analyzer.py META_KEYS).</summary>
    private static readonly string[] MetaKeys = [
        "rfp_ref", "title", "department", "publish_date", "prebid_meeting", "prebid_query_deadline", "bid_due",
        "tech_bid_opening", "presentation_date", "fin_bid_opening", "emd", "emd_exemption", "tender_fee",
        "estimated_budget", "performance_security", "selection_method", "min_technical_score", "bid_validity",
        "contract_period", "submission_mode", "scope_summary",
    ];

    private async Task<List<JsonObject>> SaveDocumentsAsync(List<IFormFile> files, List<string>? kinds, CancellationToken ct)
    {
        foreach (var f in files)       // check every file first, so a bad one does not leave the others half-saved
        {
            var ext = Path.GetExtension(f.FileName).ToLowerInvariant();
            if (!FileStore.AllowedExtensions.Contains(ext)) throw new InvalidOperationException($"File type {ext} is not supported ({f.FileName})");
        }
        var saved = new List<JsonObject>();
        for (var i = 0; i < files.Count; i++)
        {
            var kind = kinds is not null && i < kinds.Count && DocumentKinds.Contains(kinds[i]) ? kinds[i] : "rfp";
            saved.Add(new JsonObject
            {
                ["path"] = await store.SaveAsync(files[i], "rfp", ct), ["name"] = files[i].FileName, ["kind"] = kind,
                ["uploaded_at"] = DateTime.UtcNow.ToString("O"),
            });
        }
        return saved;
    }

    [HttpPost]
    [RequestSizeLimit(300L * 1024 * 1024)]
    public async Task<IActionResult> Create([FromForm] List<IFormFile> files, [FromForm] List<string>? kinds, [FromForm] string? title,
        CancellationToken ct)
    {
        if (files.Count == 0) return BadRequest(new { message = "Upload the RFP and any corrigenda" });
        List<JsonObject> saved;
        try { saved = await SaveDocumentsAsync(files, kinds, ct); }
        catch (InvalidOperationException ex) { return BadRequest(new { message = ex.Message }); }
        var bid = new Bid
        {
            Title = string.IsNullOrWhiteSpace(title) ? Path.GetFileNameWithoutExtension(files[0].FileName) : title.Trim(),
            RfpFiles = new JsonArray([.. saved]).ToJsonString(),
            Status = "new",
            CreatedBy = User.UserId(),
            CreatedAt = DateTime.UtcNow,
            UpdatedAt = DateTime.UtcNow,
        };
        db.Bids.Add(bid);
        await db.SaveChangesAsync(ct);
        var job = await jobs.EnqueueAsync("analyze_rfp", new { bid_id = bid.Id }, User.UserId(), ct);
        audit.Log(User.Actor(), "bid.created", "bid", bid.Id, new { files = files.Count });
        await db.SaveChangesAsync(ct);
        return Ok(new { bid = Summary(bid), jobId = job.Id });
    }

    [HttpGet("{id:guid}")]
    public async Task<IActionResult> Get(Guid id, CancellationToken ct)
    {
        var b = await db.Bids.AsNoTracking().SingleOrDefaultAsync(x => x.Id == id, ct);
        if (b is null) return NotFound();
        var sections = await db.BidSections.AsNoTracking().Include(s => s.Documents)
            .Where(s => s.BidId == id).OrderBy(s => s.Position).ToListAsync(ct);
        var docIds = sections.SelectMany(s => s.Documents).Select(d => d.DocumentId).Distinct().ToList();
        var docs = await db.LibraryDocuments.AsNoTracking().Where(d => docIds.Contains(d.Id))
            .Select(d => new { d.Id, d.Title, d.PageCount, d.DocType, d.Category, d.ValidUntil, d.Status })
            .ToDictionaryAsync(d => d.Id, ct);
        var jobFilter = JsonSerializer.Serialize(new { bid_id = id });
        var jobsList = await db.Jobs.AsNoTracking()
            .Where(j => EF.Functions.JsonContains(j.Payload, jobFilter))
            .OrderByDescending(j => j.Id).Take(10)
            .Select(j => new { j.Id, j.Type, j.Status, j.Error, j.WaitingEgressId, j.CreatedAt, j.FinishedAt })
            .ToListAsync(ct);
        return Ok(new
        {
            bid = Summary(b),
            analysis = Json(b.Analysis),
            settings = Json(b.Settings),
            rfpFiles = Json(b.RfpFiles),
            sections = sections.Select(s => new
            {
                s.Id, s.Position, s.Kind, s.Title, s.Html, s.Include, Sources = Json(s.Sources), s.UpdatedAt,
                Documents = s.Documents.OrderBy(d => d.Position)
                    .Where(d => docs.ContainsKey(d.DocumentId)).Select(d => docs[d.DocumentId]),
            }),
            jobs = jobsList,
        });
    }

    /// <summary>Add documents to a tender (a corrigendum issued later, a missing annexure) and analyse it again.</summary>
    [HttpPost("{id:guid}/documents")]
    [RequestSizeLimit(300L * 1024 * 1024)]
    public async Task<IActionResult> AddDocuments(Guid id, [FromForm] List<IFormFile> files, [FromForm] List<string>? kinds, CancellationToken ct)
    {
        var b = await db.Bids.FindAsync([id], ct);
        if (b is null) return NotFound();
        if (files.Count == 0) return BadRequest(new { message = "Choose at least one document" });
        List<JsonObject> saved;
        try { saved = await SaveDocumentsAsync(files, kinds, ct); }
        catch (InvalidOperationException ex) { return BadRequest(new { message = ex.Message }); }
        var list = JsonNode.Parse(b.RfpFiles)!.AsArray();
        foreach (var s in saved) list.Add(s);
        b.RfpFiles = list.ToJsonString();
        b.UpdatedAt = DateTime.UtcNow;
        var job = await jobs.EnqueueAsync("analyze_rfp", new { bid_id = id }, User.UserId(), ct);
        audit.Log(User.Actor(), "bid.documents_added", "bid", id, new { files = files.Count });
        await db.SaveChangesAsync(ct);
        return Ok(new { jobId = job.Id });
    }

    /// <summary>Remove one document from a tender and analyse it again.</summary>
    [HttpDelete("{id:guid}/documents")]
    public async Task<IActionResult> RemoveDocument(Guid id, [FromQuery] string path, CancellationToken ct)
    {
        var b = await db.Bids.FindAsync([id], ct);
        if (b is null) return NotFound();
        var list = JsonNode.Parse(b.RfpFiles)!.AsArray();
        var entry = list.FirstOrDefault(x => (string?)x?["path"] == path);
        if (entry is null) return NotFound();
        if (list.Count == 1) return BadRequest(new { message = "A tender needs at least one document; add another before removing this one" });
        list.Remove(entry);
        b.RfpFiles = list.ToJsonString();
        b.UpdatedAt = DateTime.UtcNow;
        var job = await jobs.EnqueueAsync("analyze_rfp", new { bid_id = id }, User.UserId(), ct);
        audit.Log(User.Actor(), "bid.document_removed", "bid", id, new { name = (string?)entry["name"] });
        await db.SaveChangesAsync(ct);
        store.Delete(path);
        return Ok(new { jobId = job.Id });
    }

    [HttpGet("{id:guid}/documents/file")]
    public async Task<IActionResult> DocumentFile(Guid id, [FromQuery] string path, CancellationToken ct)
    {
        var b = await db.Bids.AsNoTracking().SingleOrDefaultAsync(x => x.Id == id, ct);
        if (b is null) return NotFound();
        var entry = JsonNode.Parse(b.RfpFiles)!.AsArray().FirstOrDefault(x => (string?)x?["path"] == path);
        if (entry is null) return NotFound();
        var name = (string?)entry["name"] ?? "document";
        var type = Path.GetExtension(name).ToLowerInvariant() == ".pdf" ? "application/pdf" : "application/octet-stream";
        return PhysicalFile(store.Resolve(path), type, name);
    }

    /// <summary>The bid folder written when the PDF is built (ai-service pdf/assembler.py export_folder): each
    /// section and annexure document as its own PDF, the complete signed bid and the RFP documents.</summary>
    [HttpGet("{id:guid}/folder")]
    public async Task<IActionResult> Folder(Guid id, CancellationToken ct)
    {
        if (!await db.Bids.AnyAsync(x => x.Id == id, ct)) return NotFound();
        var index = FolderIndex(id);
        return index is null ? Ok(new { files = Array.Empty<object>() }) : Ok(index);
    }

    [HttpGet("{id:guid}/folder/file")]
    public async Task<IActionResult> FolderFile(Guid id, [FromQuery] string name, CancellationToken ct)
    {
        if (!await db.Bids.AnyAsync(x => x.Id == id, ct)) return NotFound();
        var listed = FolderIndex(id)?["files"]?.AsArray().Any(f => (string?)f?["name"] == name) ?? false;
        if (!listed) return NotFound();
        var type = name.EndsWith(".pdf", StringComparison.OrdinalIgnoreCase) ? "application/pdf" : "application/octet-stream";
        return PhysicalFile(store.Resolve($"bids/{id}/folder/{name}"), type, Path.GetFileName(name));
    }

    [HttpGet("{id:guid}/folder/zip")]
    public async Task<IActionResult> FolderZip(Guid id, CancellationToken ct)
    {
        if (!await db.Bids.AnyAsync(x => x.Id == id, ct)) return NotFound();
        var index = FolderIndex(id);
        if (index is null) return NotFound();
        return PhysicalFile(store.Resolve($"bids/{id}/bid-folder.zip"), "application/zip", $"{(string?)index["folder"] ?? "Bid documents"}.zip");
    }

    private JsonObject? FolderIndex(Guid id)
    {
        var path = store.Resolve($"bids/{id}/folder.json");
        return System.IO.File.Exists(path) ? JsonNode.Parse(System.IO.File.ReadAllText(path))?.AsObject() : null;
    }

    [HttpPost("{id:guid}/analyze")]
    public async Task<IActionResult> Analyze(Guid id, CancellationToken ct)
    {
        var b = await db.Bids.FindAsync([id], ct);
        if (b is null) return NotFound();
        var job = await jobs.EnqueueAsync("analyze_rfp", new { bid_id = id }, User.UserId(), ct);
        audit.Log(User.Actor(), "bid.reanalysis_requested", "bid", id, new { });
        await db.SaveChangesAsync(ct);
        return Ok(new { jobId = job.Id });
    }

    /// <summary>Stage 2: match the RFP's criteria against the library (local rules; see ai-service rfp/analyzer.py).</summary>
    [HttpPost("{id:guid}/eligibility")]
    public async Task<IActionResult> Eligibility(Guid id, CancellationToken ct)
    {
        var b = await db.Bids.FindAsync([id], ct);
        if (b is null) return NotFound();
        if (JsonNode.Parse(b.Analysis)?["extracted"] is null) return Conflict(new { message = "Analyse the RFP first" });
        var job = await jobs.EnqueueAsync("match_rfp", new { bid_id = id }, User.UserId(), ct);
        audit.Log(User.Actor(), "bid.eligibility_requested", "bid", id, new { });
        await db.SaveChangesAsync(ct);
        return Ok(new { jobId = job.Id });
    }

    public record EligibilityDecision(string? Status, List<Guid>? Evidence, Guid? DocumentId, string? Note, bool Reset = false);

    // the same fields and keys as ai-service rfp/matcher.py (DECIDED_FIELDS, IDENTITY, decision_key)
    private static readonly Dictionary<string, string[]> DecidedFields = new()
    {
        ["pq"] = ["status", "evidence", "note"],
        ["personnel"] = ["status", "document_id", "name", "years", "note"],
    };
    private static readonly Dictionary<string, string[]> Identity = new() { ["pq"] = ["clause", "text"], ["personnel"] = ["role", "qualification"] };
    private static readonly HashSet<string> PqResults = ["met", "partial", "gap", "undertaking"];
    private static readonly HashSet<string> PersonResults = ["met", "partial", "gap"];

    private static string KeyPart(JsonNode? n) => n is null ? "" : n is JsonValue v && v.TryGetValue<string>(out var s) ? s : n.ToJsonString();

    /// <summary>A person confirms or corrects one eligibility result: the result and the library documents it rests on
    /// (a PQ criterion), or the CV proposed (key personnel). Saved under analysis.decisions, so it applies again when
    /// eligibility is checked again or the RFP is re-read; the automatic result is kept under "auto". Reset goes back to it.</summary>
    [HttpPut("{id:guid}/eligibility/{kind}/{index:int}")]
    public async Task<IActionResult> Decide(Guid id, string kind, int index, EligibilityDecision req, CancellationToken ct)
    {
        if (!DecidedFields.TryGetValue(kind, out var fields)) return NotFound();
        if (!req.Reset && (req.Status is null || !(kind == "pq" ? PqResults : PersonResults).Contains(req.Status)))
            return BadRequest(new { message = "Unknown result" });
        var note = req.Note?.Trim() ?? "";
        var chosen = new JsonObject { ["status"] = req.Status, ["note"] = note[..Math.Min(note.Length, 500)] };
        if (!req.Reset && kind == "pq")
        {
            var ids = (req.Evidence ?? []).Distinct().Take(40).ToList();
            var usable = await Attachable(id).CountAsync(d => ids.Contains(d.Id) && d.Status == "ready" && d.Sensitivity != "restricted", ct);
            if (usable < ids.Count) return BadRequest(new { message = "Restricted documents and documents still being read cannot be enclosed" });
            chosen["evidence"] = new JsonArray(ids.Select(i => (JsonNode?)JsonValue.Create(i.ToString())).ToArray());
        }
        if (!req.Reset && kind == "personnel")
        {
            chosen["document_id"] = null; chosen["name"] = null; chosen["years"] = null;
            if (req.DocumentId is Guid cvId)
            {
                var cv = await db.LibraryDocuments.FindAsync([cvId], ct);
                if (cv is null || cv.Category != "hr" || cv.Status != "ready" || cv.Sensitivity == "restricted")
                    return BadRequest(new { message = "Choose a CV from the HR library" });
                var meta = JsonNode.Parse(cv.Metadata);
                chosen["document_id"] = cv.Id.ToString();
                chosen["name"] = KeyPart(meta?["name"]) is { Length: > 0 } name ? name : cv.Title;
                chosen["years"] = meta?["total_years"]?.DeepClone();
            }
        }

        await using var tx = await db.Database.BeginTransactionAsync(ct);
        // the eligibility check in the worker rewrites the match too: one at a time
        await db.Database.ExecuteSqlInterpolatedAsync($"SELECT 1 FROM bids WHERE id = {id} FOR UPDATE", ct);
        var b = await db.Bids.FindAsync([id], ct);
        if (b is null) return NotFound();
        var analysis = JsonNode.Parse(b.Analysis) as JsonObject ?? new JsonObject();
        if (analysis["match"]?[kind] is not JsonArray rows || index < 0 || index >= rows.Count || rows[index] is not JsonObject row)
            return Conflict(new { message = "Check eligibility first" });
        var key = string.Join("|", Identity[kind].Select(k => KeyPart(row[k])));
        if (analysis["decisions"] is not JsonObject decisions) analysis["decisions"] = decisions = new JsonObject();
        if (decisions[kind] is not JsonObject ofKind) decisions[kind] = ofKind = new JsonObject();
        if (req.Reset)
        {
            if (row["auto"] is JsonObject auto) foreach (var f in fields) row[f] = auto[f]?.DeepClone();
            row.Remove("auto"); row.Remove("decided"); ofKind.Remove(key);
        }
        else
        {
            row["auto"] ??= new JsonObject(fields.Select(f => KeyValuePair.Create(f, row[f]?.DeepClone())));
            chosen["by"] = User.Actor();
            chosen["at"] = DateTime.UtcNow.ToString("o");
            foreach (var f in fields) row[f] = chosen[f]?.DeepClone();
            row["decided"] = new JsonObject { ["by"] = User.Actor(), ["at"] = (string?)chosen["at"] };
            ofKind[key] = chosen;
        }
        var counts = new JsonObject();       // the summary counts PQ results, as the matcher's does
        foreach (var g in (analysis["match"]!["pq"] as JsonArray ?? new JsonArray()).GroupBy(r => KeyPart(r?["status"])))
            counts[g.Key] = g.Count();
        analysis["match"]!["summary"] = counts;
        b.Analysis = analysis.ToJsonString();
        b.UpdatedAt = DateTime.UtcNow;
        audit.Log(User.Actor(), req.Reset ? "bid.eligibility_reset" : "bid.eligibility_decided", "bid", id,
            new { kind, criterion = key, status = req.Status });
        await db.SaveChangesAsync(ct);
        // an existing draft follows: that criterion's documents and separator page (or the CV annexure) are
        // updated in place by the worker (ai-service drafting/sync.py); the rest of the draft is left as it is
        var drafted = await db.BidSections.AnyAsync(s => s.BidId == id, ct);
        if (drafted)
            await jobs.EnqueueAsync("sync_draft", kind == "pq" ? new { bid_id = id, pq = new[] { index } } : (object)new { bid_id = id, personnel = true },
                User.UserId(), ct);
        await tx.CommitAsync(ct);
        return Ok(new { analysis = Json(b.Analysis), draftUpdating = drafted });
    }

    /// <summary>Correct the tender's facts. Corrections are kept when the RFP is analysed again.</summary>
    [HttpPut("{id:guid}/rfp")]
    public async Task<IActionResult> UpdateRfp(Guid id, [FromBody] Dictionary<string, string?> values, CancellationToken ct)
    {
        var b = await db.Bids.FindAsync([id], ct);
        if (b is null) return NotFound();
        var patch = new JsonObject();
        foreach (var (key, raw) in values)
        {
            if (!MetaKeys.Contains(key)) return BadRequest(new { message = $"Unknown field {key}" });
            patch[key] = string.IsNullOrWhiteSpace(raw) ? null : raw.Trim();
        }
        // Only these keys change, inside the stored analysis and in one statement: an eligibility check or a
        // re-analysis finishing meanwhile is not overwritten (the analyzer reads "edited" again before it writes).
        var json = patch.ToJsonString();
        var updated = await db.Database.ExecuteSqlInterpolatedAsync($@"UPDATE bids SET
            analysis = jsonb_set(jsonb_set(analysis, '{{extracted,meta}}', (analysis #> '{{extracted,meta}}') || {json}::jsonb),
                                 '{{edited}}', COALESCE(analysis -> 'edited', '{{}}'::jsonb) || {json}::jsonb)
            WHERE id = {id} AND jsonb_typeof(analysis #> '{{extracted,meta}}') = 'object'", ct);
        if (updated == 0) return Conflict(new { message = "Analyse the RFP first" });
        string? Value(string key) => (string?)patch[key];
        if (values.ContainsKey("title") && Value("title") is { } t) b.Title = t;
        if (values.ContainsKey("rfp_ref")) b.RfpRef = Value("rfp_ref");
        if (values.ContainsKey("department")) b.Department = Value("department");
        if (values.ContainsKey("bid_due") && DueDate(Value("bid_due")) is { } d) b.DueAt = d;
        b.UpdatedAt = DateTime.UtcNow;
        audit.Log(User.Actor(), "bid.rfp_corrected", "bid", id, new { fields = values.Keys });
        await db.SaveChangesAsync(ct);
        await db.Entry(b).ReloadAsync(ct);
        return Ok(new { analysis = Json(b.Analysis), bid = Summary(b) });
    }

    private static readonly string[] DueFormats = BuildDueFormats();

    private static string[] BuildDueFormats()
    {
        var dates = new[] { "d/M/yyyy", "d-M-yyyy", "d.M.yyyy", "d MMM yyyy", "d MMMM yyyy", "MMM d yyyy", "yyyy-MM-dd" };
        var times = new[] { "", " H:mm", " HH:mm", " h:mm tt", " hh:mm tt", " h tt", " H.mm" };
        return dates.SelectMany(d => times.Select(t => d + t)).ToArray();
    }

    /// <summary>Indian tender dates as written: "15/11/2026", "11.11.2026, up to 05:00 PM", "15 Nov 2026 15:00 hrs".
    /// Explicit formats (the API runs with invariant globalization) read as Indian time, whatever the server's zone.</summary>
    private static DateTime? DueDate(string? text)
    {
        if (string.IsNullOrWhiteSpace(text)) return null;
        var t = System.Text.RegularExpressions.Regex.Replace(text, @"(?i)\b(hrs?|hours|ist|up\s*to|upto|till|by|onwards|at)\b\.?", " ");
        t = System.Text.RegularExpressions.Regex.Replace(t, @"(\d)(st|nd|rd|th)\b", "$1");
        t = System.Text.RegularExpressions.Regex.Replace(t, @"[,()]", " ");
        t = System.Text.RegularExpressions.Regex.Replace(t, @"\s+", " ").Trim();
        return DateTime.TryParseExact(t, DueFormats, System.Globalization.CultureInfo.InvariantCulture,
                System.Globalization.DateTimeStyles.AllowWhiteSpaces, out var d)
            ? new DateTimeOffset(d, TimeSpan.FromHours(5.5)).UtcDateTime : null;
    }

    [HttpPost("{id:guid}/draft")]
    public async Task<IActionResult> Draft(Guid id, DraftRequest req, CancellationToken ct)
    {
        var b = await db.Bids.FindAsync([id], ct);
        if (b is null) return NotFound();
        if (b.Status is "new" or "analysing") return Conflict(new { message = "The RFP analysis has not finished" });
        var filter = JsonSerializer.Serialize(new { bid_id = id });
        if (await db.Jobs.AnyAsync(j => j.Type == "draft_bid" && (j.Status == "queued" || j.Status == "running" || j.Status == "awaiting_approval")
                                        && EF.Functions.JsonContains(j.Payload, filter), ct))
            return Conflict(new { message = "A draft is already being prepared for this bid" });
        var job = await jobs.EnqueueAsync("draft_bid", new { bid_id = id, overwrite = req.Overwrite }, User.UserId(), ct);
        audit.Log(User.Actor(), "bid.draft_requested", "bid", id, new { req.Overwrite });
        await db.SaveChangesAsync(ct);
        return Ok(new { jobId = job.Id });
    }

    [HttpPost("{id:guid}/assemble")]
    public async Task<IActionResult> Assemble(Guid id, CancellationToken ct)
    {
        if (!await db.BidSections.AnyAsync(s => s.BidId == id, ct)) return Conflict(new { message = "Generate the draft first" });
        var job = await jobs.EnqueueAsync("assemble_pdf", new { bid_id = id }, User.UserId(), ct);
        return Ok(new { jobId = job.Id });
    }

    /// <summary>The signed and stamped PDF, or with ?raw=true the unstamped pages the preview overlays live.</summary>
    [HttpGet("{id:guid}/pdf")]
    public async Task<IActionResult> Pdf(Guid id, [FromQuery] bool raw, CancellationToken ct)
    {
        var b = await db.Bids.AsNoTracking().SingleOrDefaultAsync(x => x.Id == id, ct);
        if (b?.OutputPdfPath is null) return NotFound();
        if (raw) return PhysicalFile(store.Resolve(Sibling(b.OutputPdfPath, "technical-bid.raw.pdf")), "application/pdf");
        var name = $"Technical-bid-{(b.RfpRef ?? b.Title).Replace('/', '-')}.pdf";
        return PhysicalFile(store.Resolve(b.OutputPdfPath), "application/pdf", name, enableRangeProcessing: true);
    }

    /// <summary>Page map of the last build: kind and annexure tag per page, section start and length.</summary>
    [HttpGet("{id:guid}/pages")]
    public async Task<IActionResult> Pages(Guid id, CancellationToken ct)
    {
        var b = await db.Bids.AsNoTracking().SingleOrDefaultAsync(x => x.Id == id, ct);
        if (b?.OutputPdfPath is null) return NotFound();
        var path = store.Resolve(Sibling(b.OutputPdfPath, "pages.json"));
        if (!System.IO.File.Exists(path)) return NotFound();
        return Content(await System.IO.File.ReadAllTextAsync(path, ct), "application/json");
    }

    public record DscPrepare(string Certificate, List<string>? Chain);
    public record DscFinish(string Session, string Signature);

    /// <summary>Digital signature with a DSC token, step 1 (ai-service pdf/dsc.py): the hash that the BidCraft Signer
    /// app on the person's computer signs with the token. The token's key never comes here.</summary>
    [HttpPost("{id:guid}/dsc/prepare")]
    public async Task<IActionResult> DscPrepareAsync(Guid id, DscPrepare req, CancellationToken ct)
    {
        var b = await db.Bids.AsNoTracking().SingleOrDefaultAsync(x => x.Id == id, ct);
        if (b is null) return NotFound();
        if (b.OutputPdfPath is null) return Conflict(new { message = "Build the PDF first" });
        var filter = JsonSerializer.Serialize(new { bid_id = id });
        if (await db.Jobs.AnyAsync(j => j.Type == "assemble_pdf" && (j.Status == "queued" || j.Status == "running")
                                        && EF.Functions.JsonContains(j.Payload, filter), ct))
            return Conflict(new { message = "The PDF is being built; sign it when that is done" });
        var r = await ai.TryPostAsync("/dsc/prepare", new { bid_id = id, certificate = req.Certificate, chain = req.Chain ?? [] }, ct);
        return r.Ok ? Ok(r.Body) : BadRequest(new { message = r.Message });
    }

    /// <summary>Step 2: the token's signature goes into the PDF, which is verified and replaces the bid's PDF.</summary>
    [HttpPost("{id:guid}/dsc/finish")]
    public async Task<IActionResult> DscFinishAsync(Guid id, DscFinish req, CancellationToken ct)
    {
        if (!await db.Bids.AnyAsync(x => x.Id == id, ct)) return NotFound();
        var r = await ai.TryPostAsync("/dsc/finish", new { bid_id = id, session = req.Session, signature = req.Signature }, ct);
        if (!r.Ok) return BadRequest(new { message = r.Message });
        audit.Log(User.Actor(), "bid.dsc_signed", "bid", id, new
        {
            signer = r.Body.TryGetProperty("signer", out var s) ? s.GetString() : null,
            serial = r.Body.TryGetProperty("serial", out var n) ? n.GetString() : null,
        });
        await db.SaveChangesAsync(ct);
        return Ok(r.Body);
    }

    [HttpGet("{id:guid}/signature")]
    public async Task<IActionResult> SignatureImage(Guid id, CancellationToken ct)
    {
        var b = await db.Bids.AsNoTracking().SingleOrDefaultAsync(x => x.Id == id, ct);
        if (b is null) return NotFound();
        var path = JsonNode.Parse(b.Settings)?["sign"]?["image_path"]?.GetValue<string>();
        if (string.IsNullOrEmpty(path)) return NotFound();
        return PhysicalFile(store.Resolve(path), path.EndsWith(".png") ? "image/png" : "image/jpeg");
    }

    public record RedraftRequest(string? Instruction, string? Tone);

    [HttpPost("{id:guid}/sections/{sid:guid}/redraft")]
    public async Task<IActionResult> Redraft(Guid id, Guid sid, RedraftRequest req, CancellationToken ct)
    {
        var s = await db.BidSections.AsNoTracking().SingleOrDefaultAsync(x => x.Id == sid && x.BidId == id, ct);
        if (s is null) return NotFound();
        if (s.Kind != "text") return BadRequest(new { message = "Only text sections can be redrafted" });
        var job = await jobs.EnqueueAsync("redraft_section",
            new { bid_id = id, section_id = sid, instruction = req.Instruction ?? "", tone = req.Tone ?? "formal" }, User.UserId(), ct);
        audit.Log(User.Actor(), "section.redraft_requested", "section", sid, new { req.Tone });
        await db.SaveChangesAsync(ct);
        return Ok(new { jobId = job.Id });
    }

    private static string Sibling(string relative, string name) => Path.Combine(Path.GetDirectoryName(relative) ?? "", name);

    [HttpPut("{id:guid}/settings")]
    public async Task<IActionResult> Settings(Guid id, [FromBody] JsonElement settings, CancellationToken ct)
    {
        var b = await db.Bids.FindAsync([id], ct);
        if (b is null) return NotFound();
        if (settings.ValueKind != JsonValueKind.Object) return BadRequest(new { message = "Settings must be an object" });
        // keep the stored signature image path; the browser cannot set file paths
        var incoming = JsonNode.Parse(settings.GetRawText())!.AsObject();
        if (incoming["sign"] is { } signNode && signNode is not JsonObject) return BadRequest(new { message = "sign must be an object" });
        var current = JsonNode.Parse(string.IsNullOrEmpty(b.Settings) ? "{}" : b.Settings) as JsonObject;
        var imagePath = ((current?["sign"] as JsonObject)?["image_path"] as JsonValue)?.TryGetValue<string>(out var ip) == true ? ip : null;
        if (incoming["sign"] is JsonObject sign)
        {
            // the saved signature and stamp images chosen (api/sign-images)
            foreach (var key in new[] { "image_id", "stamp_id" })
                if (sign[key] is { } v && !(v is JsonValue s && s.TryGetValue<string>(out var text) && Guid.TryParse(text, out _)))
                    return BadRequest(new { message = $"{key} must be the id of a saved image" });
            sign["image_path"] = imagePath;
        }
        else if (imagePath is not null) incoming["sign"] = new JsonObject { ["image_path"] = imagePath };
        b.Settings = incoming.ToJsonString();
        b.UpdatedAt = DateTime.UtcNow;
        await db.SaveChangesAsync(ct);
        return Ok(Json(b.Settings));
    }

    // ---- sections -------------------------------------------------------

    [HttpPut("{id:guid}/sections/{sid:guid}")]
    public async Task<IActionResult> UpdateSection(Guid id, Guid sid, SectionUpdate req, CancellationToken ct)
    {
        var s = await db.BidSections.SingleOrDefaultAsync(x => x.Id == sid && x.BidId == id, ct);
        if (s is null) return NotFound();
        try
        {
            // the same whitelist the PDF build applies, so the builder never shows script or event handlers
            var clean = await ai.PostAsync("/sanitize", new { html = req.Html ?? "" }, ct);
            s.Html = clean.GetProperty("html").GetString() ?? "";
        }
        catch (HttpRequestException)
        {
            return StatusCode(503, new { message = "The section could not be checked right now; try saving again in a moment" });
        }
        s.Title = req.Title.Trim();
        s.Include = req.Include;
        s.UpdatedAt = DateTime.UtcNow;
        await db.SaveChangesAsync(ct);
        return NoContent();
    }

    [HttpPost("{id:guid}/sections")]
    public async Task<IActionResult> AddSection(Guid id, NewSection req, CancellationToken ct)
    {
        if (req.Kind is not ("text" or "docs")) return BadRequest(new { message = "Kind must be text or docs" });
        if (!await db.Bids.AnyAsync(x => x.Id == id, ct)) return NotFound();
        var sections = await db.BidSections.Where(s => s.BidId == id).OrderBy(s => s.Position).ToListAsync(ct);
        var after = req.AfterId is null ? -1 : sections.FindIndex(s => s.Id == req.AfterId);
        if (req.AfterId is not null && after < 0) return BadRequest(new { message = "The section to insert after is not in this bid" });
        var index = req.AfterId is null ? sections.Count : after + 1;
        var section = new BidSection
        {
            BidId = id, Kind = req.Kind, Title = string.IsNullOrWhiteSpace(req.Title) ? "New section" : req.Title.Trim(),
            Html = req.Kind == "text" ? "<p></p>" : "", UpdatedAt = DateTime.UtcNow,
        };
        sections.Insert(Math.Clamp(index, 0, sections.Count), section);
        for (var i = 0; i < sections.Count; i++) sections[i].Position = i;
        db.BidSections.Add(section);
        await db.SaveChangesAsync(ct);
        return Ok(new { section.Id });
    }

    [HttpDelete("{id:guid}/sections/{sid:guid}")]
    public async Task<IActionResult> DeleteSection(Guid id, Guid sid, CancellationToken ct)
    {
        var s = await db.BidSections.SingleOrDefaultAsync(x => x.Id == sid && x.BidId == id, ct);
        if (s is null) return NotFound();
        db.BidSections.Remove(s);
        audit.Log(User.Actor(), "bid.section_deleted", "bid", id, new { s.Title, s.Kind });
        await db.SaveChangesAsync(ct);
        return NoContent();
    }

    [HttpPost("{id:guid}/sections/reorder")]
    public async Task<IActionResult> ReorderSections(Guid id, Reorder req, CancellationToken ct)
    {
        var sections = await db.BidSections.Where(s => s.BidId == id).ToListAsync(ct);
        if (sections.Count != req.Ids.Count || sections.Any(s => !req.Ids.Contains(s.Id)))
            return BadRequest(new { message = "Send every section id exactly once" });
        foreach (var s in sections) s.Position = req.Ids.IndexOf(s.Id);
        await db.SaveChangesAsync(ct);
        return NoContent();
    }

    [HttpPut("{id:guid}/sections/{sid:guid}/documents")]
    public async Task<IActionResult> SetDocuments(Guid id, Guid sid, SectionDocuments req, CancellationToken ct)
    {
        var s = await db.BidSections.Include(x => x.Documents).SingleOrDefaultAsync(x => x.Id == sid && x.BidId == id, ct);
        if (s is null) return NotFound();
        if (s.Kind != "docs") return BadRequest(new { message = "Only annexure sections hold documents" });
        var known = await Attachable(id).Where(d => req.DocumentIds.Contains(d.Id)).Select(d => d.Id).ToListAsync(ct);
        db.BidSectionDocuments.RemoveRange(s.Documents);
        var position = 0;
        foreach (var docId in req.DocumentIds.Where(known.Contains).Distinct())
            db.BidSectionDocuments.Add(new BidSectionDocument { SectionId = sid, DocumentId = docId, Position = position++ });
        s.UpdatedAt = DateTime.UtcNow;
        await db.SaveChangesAsync(ct);
        return NoContent();
    }
}
