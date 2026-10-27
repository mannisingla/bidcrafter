import { Injectable, signal } from '@angular/core';
import { BidSummary } from './models';

/** The bid the rail's "RFP analysis", "Bid builder" and "Preview & sign" buttons point at (like the prototype's document tab). */
@Injectable({ providedIn: 'root' })
export class CurrentBid {
  readonly bid = signal<BidSummary | null>(this.restore());
  readonly hasDraft = signal(false);

  set(bid: BidSummary | null, hasDraft = false): void {
    this.bid.set(bid);
    this.hasDraft.set(hasDraft);
    try { bid ? sessionStorage.setItem('bidcraft.bid', JSON.stringify(bid)) : sessionStorage.removeItem('bidcraft.bid'); } catch { /* ignore */ }
  }

  private restore(): BidSummary | null {
    try { const raw = sessionStorage.getItem('bidcraft.bid'); return raw ? JSON.parse(raw) : null; } catch { return null; }
  }
}
