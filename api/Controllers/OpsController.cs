using System.Text.Json;
using BidCraft.Api.Data;
using BidCraft.Api.Services;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;

namespace BidCraft.Api.Controllers;

[ApiController]
[Route("api/jobs")]
[Authorize]
public class JobsController(AppDb db) : ControllerBase
{
    [HttpGet("{id:long}")]
    public async Task<IActionResult> Get(long id, CancellationToken ct)
    {
        var j = await db.Jobs.AsNoTracking().SingleOrDefaultAsync(x => x.Id == id, ct);
        return j is null ? NotFound() : Ok(new { j.Id, j.Type, j.Status, j.Error, j.WaitingEgressId, j.CreatedAt, j.FinishedAt });
    }
}

[ApiController]
[Route("api/audit")]
[Authorize(Policy = "Reviewer")]
public class AuditController(AppDb db) : ControllerBase
{
    [HttpGet]
    public async Task<IActionResult> List([FromQuery] int take, CancellationToken ct)
    {
        var rows = await db.AuditLog.AsNoTracking().OrderByDescending(a => a.At).Take(Math.Clamp(take == 0 ? 200 : take, 1, 1000))
            .ToListAsync(ct);
        return Ok(rows.Select(a => new { a.Id, a.At, a.Actor, a.Action, a.Entity, a.EntityId, Detail = JsonDocument.Parse(a.Detail).RootElement }));
    }
}
