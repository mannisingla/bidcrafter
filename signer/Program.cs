// BidCraft Signer: lets the BidCraft web app sign a bid with a DSC token plugged into this computer.
//
// It listens on http://localhost:47821 and answers only the BidCraft site (BidCraftSigner.origins.txt next to the
// exe, one address per line; https://192.168.2.141:8443 if there is none). It lists the signing certificates the
// token's driver has put in the Windows certificate store, and signs one SHA-256 hash after the person confirms
// in a Windows dialog; the token's driver asks for the PIN. The private key never leaves the token, and nothing
// but the hash comes here: the PDF is prepared and finished on the BidCraft server.
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;

const int Port = 47821;
var origins = Origins(args);

var builder = WebApplication.CreateBuilder(args);
builder.WebHost.UseUrls($"http://localhost:{Port}");
builder.Logging.SetMinimumLevel(LogLevel.Warning);
var app = builder.Build();

// Only the BidCraft site may use the token through this app (browsers always send Origin on these requests).
app.Use(async (ctx, next) =>
{
    var origin = ctx.Request.Headers.Origin.ToString().TrimEnd('/');
    if (!origins.Contains(origin))
    {
        ctx.Response.StatusCode = StatusCodes.Status403Forbidden;
        await ctx.Response.WriteAsync("BidCraft Signer answers only the BidCraft site.");
        return;
    }
    ctx.Response.Headers.AccessControlAllowOrigin = origin;
    ctx.Response.Headers.Vary = "Origin";
    if (HttpMethods.IsOptions(ctx.Request.Method))
    {
        ctx.Response.Headers.AccessControlAllowMethods = "GET, POST";
        ctx.Response.Headers.AccessControlAllowHeaders = "Content-Type";
        if (ctx.Request.Headers.ContainsKey("Access-Control-Request-Private-Network"))      // Chrome, Edge
            ctx.Response.Headers["Access-Control-Allow-Private-Network"] = "true";
        ctx.Response.StatusCode = StatusCodes.Status204NoContent;
        return;
    }
    await next();
});

app.MapGet("/status", () => new { app = "BidCraft Signer", version = "1.0.0" });

app.MapGet("/certificates", () => Certificates.ForSigning().Select(c => new
{
    thumbprint = c.Thumbprint,
    subject = c.GetNameInfo(X509NameType.SimpleName, false),
    issuer = c.GetNameInfo(X509NameType.SimpleName, true),
    validTo = c.NotAfter.ToString("yyyy-MM-dd"),
    certificate = Convert.ToBase64String(c.RawData),
    chain = Certificates.Chain(c),
}));

app.MapPost("/sign", (SignRequest req) =>
{
    var cert = Certificates.ForSigning().FirstOrDefault(c => c.Thumbprint.Equals(req.Thumbprint ?? "", StringComparison.OrdinalIgnoreCase));
    if (cert is null) return Results.BadRequest(new { message = "That certificate is not available. Is the DSC token plugged in?" });
    byte[] hash;
    try { hash = Convert.FromBase64String(req.Hash ?? ""); }
    catch (FormatException) { return Results.BadRequest(new { message = "The hash to sign could not be read" }); }
    if (hash.Length != 32) return Results.BadRequest(new { message = "Expected a SHA-256 hash" });

    var who = cert.GetNameInfo(X509NameType.SimpleName, false);
    var issuer = cert.GetNameInfo(X509NameType.SimpleName, true);
    if (!Native.Confirm($"BidCraft asks to digitally sign\n\n{req.Document ?? "a bid"}\n\nwith the DSC of {who} (issued by {issuer}).\n\nSign it?"))
        return Results.Json(new { message = "Signing was cancelled on this computer" }, statusCode: StatusCodes.Status403Forbidden);
    try
    {
        using var rsa = cert.GetRSAPrivateKey() ?? throw new CryptographicException("This certificate has no RSA key");
        var signature = rsa.SignHash(hash, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);     // the token's driver asks for the PIN
        Console.WriteLine($"{DateTime.Now:dd MMM HH:mm:ss}  signed for {who}: {req.Document}");
        return Results.Ok(new { signature = Convert.ToBase64String(signature) });
    }
    catch (CryptographicException ex)
    {
        return Results.BadRequest(new { message = $"The token did not sign: {ex.Message}" });
    }
});

Console.WriteLine("BidCraft Signer is running. Keep this window open while you sign bids; close it when done.");
Console.WriteLine($"Answering: {string.Join(", ", origins)}");
try
{
    app.Run();
}
catch (IOException)
{
    Console.WriteLine($"Port {Port} is in use: BidCraft Signer is probably running already.");
    Console.ReadKey();
}

static HashSet<string> Origins(string[] args)
{
    var file = Path.Combine(AppContext.BaseDirectory, "BidCraftSigner.origins.txt");
    var listed = args.Where(a => a.StartsWith("https://") || a.StartsWith("http://"))
        .Concat(File.Exists(file) ? File.ReadAllLines(file) : [])
        .Select(o => o.Trim().TrimEnd('/')).Where(o => o.Length > 0).ToHashSet(StringComparer.OrdinalIgnoreCase);
    return listed.Count > 0 ? listed : new HashSet<string>(StringComparer.OrdinalIgnoreCase) { "https://192.168.2.141:8443" };
}

record SignRequest(string? Thumbprint, string? Hash, string? Document);

static class Certificates
{
    /// <summary>Certificates with a private key (on a token or otherwise), valid today, made for signing.</summary>
    public static List<X509Certificate2> ForSigning()
    {
        using var store = new X509Store(StoreName.My, StoreLocation.CurrentUser);
        store.Open(OpenFlags.ReadOnly | OpenFlags.OpenExistingOnly);
        var now = DateTime.Now;
        return store.Certificates
            .Where(c => c.HasPrivateKey && c.NotBefore <= now && now <= c.NotAfter && c.GetRSAPublicKey() is not null && CanSign(c))
            .OrderByDescending(c => c.NotAfter).ToList();
    }

    // a DSC token usually holds a signing and an encryption certificate: only the first signs
    private static bool CanSign(X509Certificate2 c) =>
        c.Extensions.OfType<X509KeyUsageExtension>().FirstOrDefault() is not { } usage
        || usage.KeyUsages.HasFlag(X509KeyUsageFlags.DigitalSignature) || usage.KeyUsages.HasFlag(X509KeyUsageFlags.NonRepudiation);

    /// <summary>The issuing CA certificates, for the signature to carry its chain.</summary>
    public static string[] Chain(X509Certificate2 cert)
    {
        using var chain = new X509Chain();
        chain.ChainPolicy.RevocationMode = X509RevocationMode.NoCheck;
        chain.ChainPolicy.VerificationFlags = X509VerificationFlags.AllowUnknownCertificateAuthority;
        chain.Build(cert);
        return chain.ChainElements.Skip(1).Select(e => Convert.ToBase64String(e.Certificate.RawData)).ToArray();
    }
}

static class Native
{
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int MessageBoxW(IntPtr owner, string text, string caption, uint type);

    private const uint YesNo = 0x4, Question = 0x20, Foreground = 0x10000, TopMost = 0x40000;

    public static bool Confirm(string text) => MessageBoxW(IntPtr.Zero, text, "BidCraft Signer", YesNo | Question | Foreground | TopMost) == 6;
}
