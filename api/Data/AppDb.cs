using Microsoft.EntityFrameworkCore;

namespace BidCraft.Api.Data;

public class AppDb(DbContextOptions<AppDb> options) : DbContext(options)
{
    public DbSet<User> Users => Set<User>();
    public DbSet<LibraryDocument> LibraryDocuments => Set<LibraryDocument>();
    public DbSet<Bid> Bids => Set<Bid>();
    public DbSet<BidSection> BidSections => Set<BidSection>();
    public DbSet<BidSectionDocument> BidSectionDocuments => Set<BidSectionDocument>();
    public DbSet<Job> Jobs => Set<Job>();
    public DbSet<EgressRequest> EgressRequests => Set<EgressRequest>();
    public DbSet<AuditEntry> AuditLog => Set<AuditEntry>();
    public DbSet<SignImage> SignImages => Set<SignImage>();

    protected override void OnModelCreating(ModelBuilder b)
    {
        b.Entity<AuditEntry>().ToTable("audit_log");
        b.Entity<SignImage>().ToTable("sign_images");
        b.Entity<LibraryDocument>().Property(x => x.Metadata).HasColumnType("jsonb");
        b.Entity<Bid>(e =>
        {
            e.Property(x => x.RfpFiles).HasColumnType("jsonb");
            e.Property(x => x.Analysis).HasColumnType("jsonb");
            e.Property(x => x.Settings).HasColumnType("jsonb");
        });
        b.Entity<BidSection>(e =>
        {
            e.Property(x => x.Sources).HasColumnType("jsonb");
            e.HasMany(x => x.Documents).WithOne().HasForeignKey(d => d.SectionId);
        });
        b.Entity<Job>(e =>
        {
            e.Property(x => x.Payload).HasColumnType("jsonb");
            e.Property(x => x.Result).HasColumnType("jsonb");
        });
        b.Entity<EgressRequest>(e =>
        {
            e.Property(x => x.RedactedPayload).HasColumnType("jsonb");
            e.Property(x => x.RedactionSummary).HasColumnType("jsonb");
            e.Property(x => x.SourceRefs).HasColumnType("jsonb");
            e.Property(x => x.ResponseRedacted).HasColumnType("jsonb");
            e.Property(x => x.ResponseMeta).HasColumnType("jsonb");
        });
        b.Entity<AuditEntry>().Property(x => x.Detail).HasColumnType("jsonb");
    }
}
