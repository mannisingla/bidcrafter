import { DatePipe } from '@angular/common';
import { Component, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { Api } from '../core/api';
import { CurrentBid } from '../core/current-bid';
import { Icons } from '../core/icons';
import { BidSummary } from '../core/models';
import { STATUS_LABEL, chipClass, dueIn } from './shared';

/** Every RFP analysed so far, newest first; opening one leads to its analysis and then its bid response. */
@Component({
  selector: 'bc-bids-list',
  imports: [DatePipe, Icons, RouterLink],
  template: `
    <div class="wrap">
      <div class="row between wrapx" style="margin-bottom:14px">
        <div><h1 class="v-title">RFP analysis</h1><p class="sub">Tenders read so far. Open one to see what it asks for, then prepare the bid response.</p></div>
        <a class="btn primary" routerLink="/bids/new"><lucide-icon name="file-search"></lucide-icon>Analyse a new RFP</a>
      </div>
      <div class="card" style="padding:4px 8px">
        @if (bids().length) {
          <div class="tbl-wrap"><table class="tbl"><tr><th>RFP</th><th>Department</th><th>Bid due</th><th>Stage</th><th>Added</th></tr>
            @for (b of bids(); track b.id) {
              <tr class="click" (click)="open(b)">
                <td class="t1">{{ b.title }}@if (b.rfpRef) { <div class="muted small">{{ b.rfpRef }}</div> }</td>
                <td>{{ b.department || '—' }}</td>
                <td style="white-space:nowrap">{{ b.dueAt ? (b.dueAt | date: 'dd MMM yyyy') : '—' }}@if (dueIn(b); as due) { <div><span [class]="due.cls">{{ due.text }}</span></div> }</td>
                <td><span [class]="chip(b.status)">{{ stage(b.status) }}</span></td>
                <td style="white-space:nowrap">{{ b.createdAt | date: 'dd MMM yyyy' }}</td>
              </tr>
            }
          </table></div>
        } @else if (loaded()) {
          <div class="drop" routerLink="/bids/new"><lucide-icon name="file-search"></lucide-icon><b>No RFPs yet</b>Upload the documents of a tender to analyse it.</div>
        } @else { <p class="muted" style="padding:12px">Loading…</p> }
      </div>
    </div>`,
})
export class BidsListPage {
  private api = inject(Api);
  private router = inject(Router);
  private current = inject(CurrentBid);
  bids = signal<BidSummary[]>([]);
  loaded = signal(false);
  chip = chipClass;

  constructor() { this.api.bids().subscribe(b => { this.bids.set(b); this.loaded.set(true); }); }

  stage(status: string): string {
    return ({ new: 'Queued', analysing: 'Reading the RFP', analysed: 'RFP analysed', drafting: 'Drafting the bid', drafted: 'Bid drafted' } as Record<string, string>)[status]
      ?? STATUS_LABEL[status] ?? status;
  }
  dueIn(b: BidSummary): { cls: string; text: string } | null {
    if (!b.dueAt) return null;
    const { days, passed } = dueIn(b.dueAt);
    if (passed) return null;
    return { cls: days <= 7 ? 'chip bad' : days <= 21 ? 'chip warn' : 'chip ok', text: days === 0 ? 'today' : `${days} day${days === 1 ? '' : 's'} left` };
  }

  open(b: BidSummary): void {
    this.current.set(b, b.status !== 'new' && b.status !== 'analysing' && b.status !== 'analysed');
    this.router.navigate(['/bids', b.id]);
  }
}
