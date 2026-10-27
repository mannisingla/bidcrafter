namespace BidCraft.Api.Data;

// Tables are created by db/schema.sql; EF Core only maps them (no migrations).
// jsonb columns are mapped as raw JSON strings.

public class User
{
    public Guid Id { get; set; }
    public string Email { get; set; } = "";
    public string FullName { get; set; } = "";
    public string PasswordHash { get; set; } = "";
    public string Role { get; set; } = Roles.BidManager;
    public DateTime CreatedAt { get; set; }
}

public static class Roles
{
    public const string Admin = "admin";
    public const string BidManager = "bid_manager";
    public const string Reviewer = "reviewer";
    public static readonly string[] All = [Admin, BidManager, Reviewer];
}

public class LibraryDocument
{
    public Guid Id { get; set; }
    public string Category { get; set; } = "";
    public string DocType { get; set; } = "other";
    public string Title { get; set; } = "";
    public string FilePath { get; set; } = "";
    public string? PdfPath { get; set; }
    public string OriginalName { get; set; } = "";
    public string MimeType { get; set; } = "";
    public int? PageCount { get; set; }
    public string Status { get; set; } = "uploaded";
    public string? Error { get; set; }
    public string Metadata { get; set; } = "{}";
    public DateOnly? ValidUntil { get; set; }
    public string Sensitivity { get; set; } = "internal";
    public string? ExtractedText { get; set; }
    public string? FileSha256 { get; set; }
    public Guid? CreatedBy { get; set; }
    public DateTime CreatedAt { get; set; }
    public DateTime UpdatedAt { get; set; }

    public static readonly string[] Categories = ["experience", "statutory", "hr", "literature", "uploads"];
    public static readonly string[] Sensitivities = ["public", "internal", "confidential", "restricted"];
}

public class Bid
{
    public Guid Id { get; set; }
    public string Title { get; set; } = "";
    public string? RfpRef { get; set; }
    public string? Department { get; set; }
    public DateTime? DueAt { get; set; }
    public string Status { get; set; } = "new";
    public string? Error { get; set; }
    public string RfpFiles { get; set; } = "[]";
    public string Analysis { get; set; } = "{}";
    public string Settings { get; set; } = "{}";
    public string? OutputPdfPath { get; set; }
    public int? OutputPageCount { get; set; }
    public Guid? CreatedBy { get; set; }
    public DateTime CreatedAt { get; set; }
    public DateTime UpdatedAt { get; set; }
}

public class BidSection
{
    public Guid Id { get; set; }
    public Guid BidId { get; set; }
    public int Position { get; set; }
    public string Kind { get; set; } = "text";
    public string Title { get; set; } = "";
    public string Html { get; set; } = "";
    public bool Include { get; set; } = true;
    public string Sources { get; set; } = "[]";
    public DateTime UpdatedAt { get; set; }
    public List<BidSectionDocument> Documents { get; set; } = [];
}

public class BidSectionDocument
{
    public long Id { get; set; }
    public Guid SectionId { get; set; }
    public Guid DocumentId { get; set; }
    public int Position { get; set; }
}

public class Job
{
    public long Id { get; set; }
    public string Type { get; set; } = "";
    public string Payload { get; set; } = "{}";
    public string Status { get; set; } = "queued";
    public Guid? WaitingEgressId { get; set; }
    public string? Result { get; set; }
    public string? Error { get; set; }
    public int Attempts { get; set; }
    public Guid? CreatedBy { get; set; }
    public DateTime CreatedAt { get; set; }
    public DateTime? StartedAt { get; set; }
    public DateTime? FinishedAt { get; set; }
}

public class EgressRequest
{
    public Guid Id { get; set; }
    public Guid? BidId { get; set; }
    public long? JobId { get; set; }
    public string Purpose { get; set; } = "";
    public string Status { get; set; } = "pending";
    public string PayloadSha256 { get; set; } = "";
    public string RedactedPayload { get; set; } = "{}";
    public string RedactionSummary { get; set; } = "{}";
    public string SourceRefs { get; set; } = "[]";
    public int CharCount { get; set; }
    public string Model { get; set; } = "";
    public string? ResponseRedacted { get; set; }
    public string? ResponseMeta { get; set; }
    public string? Error { get; set; }
    public Guid? ReviewedBy { get; set; }
    public string? ReviewNote { get; set; }
    public DateTime? ReviewedAt { get; set; }
    public DateTime CreatedAt { get; set; }
    public DateTime? SentAt { get; set; }
}

/// <summary>A saved signature or stamp image, chosen for each bid on the Preview &amp; sign screen.</summary>
public class SignImage
{
    public Guid Id { get; set; }
    public string Kind { get; set; } = "signature";
    public string Name { get; set; } = "";
    public string FilePath { get; set; } = "";
    public Guid? CreatedBy { get; set; }
    public DateTime CreatedAt { get; set; }
}

public class AuditEntry
{
    public long Id { get; set; }
    public DateTime At { get; set; }
    public string Actor { get; set; } = "";
    public string Action { get; set; } = "";
    public string? Entity { get; set; }
    public string? EntityId { get; set; }
    public string Detail { get; set; } = "{}";
}
