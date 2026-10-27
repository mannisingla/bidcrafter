import { DatePipe } from '@angular/common';
import { Component, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { forkJoin } from 'rxjs';
import { Api } from '../core/api';
import { CurrentBid } from '../core/current-bid';
import { Icons } from '../core/icons';
import { BidSummary, LibraryDoc, parentOf } from '../core/models';
import { STATUS_LABEL, chipClass } from './shared';

@Component({
  selector: 'bc-home',
  imports: [RouterLink, DatePipe, Icons],
  template: `
    <div class="wrap">
      <h1 class="v-title">Bid Response Crafter</h1>
      <p class="sub">Your organisation’s experience, certificates and people, turned into a complete, signed technical bid for each RFP.</p>
      <div class="flow" style="margin-top:18px">
        <button [class.done]="docs().length" (click)="router.navigate(['/library'])"><span class="n">{{ docs().length ? '✓' : 1 }}</span><b>Train on your documents</b><small>Experience, statutory, HR and bid literature</small></button>
        <button [class.done]="current() && current()!.status !== 'new' && current()!.status !== 'analysing'" [class.cur]="!current()" (click)="router.navigate(current() ? ['/bids', current()!.id] : ['/bids/new'])"><span class="n">{{ current() && current()!.status !== 'new' ? '✓' : 2 }}</span><b>Upload the RFP</b><small>{{ current() ? 'RFP ' + (current()!.rfpRef || current()!.title) : 'The agent reads every clause' }}</small></button>
        <button [class.done]="hasDraft()" (click)="router.navigate(current() ? ['/bids', current()!.id, 'builder'] : ['/bids/new'])"><span class="n">{{ hasDraft() ? '✓' : 3 }}</span><b>Review the draft</b><small>Edit, reorder, add documents</small></button>
        <button [class.cur]="hasDraft()" (click)="router.navigate(current() ? ['/bids', current()!.id, 'preview'] : ['/bids/new'])"><span class="n">4</span><b>Sign, stamp and export</b><small>Page numbers, sign every page, one PDF</small></button>
      </div>
      <div class="section-h"><h2>Documents Library</h2><a class="btn sm quiet" routerLink="/library">Open library</a></div>
      <div class="kb">
        @for (c of categories; track c.key) {
          <div class="card" [routerLink]="['/library']" [queryParams]="{ tab: c.key }">
            <div class="row between"><div class="ico"><lucide-icon [name]="c.icon"></lucide-icon></div>
              @if (processing(c.key)) { <span class="chip blue"><span class="spin"></span>Reading</span> } @else { <span class="chip ok"><lucide-icon name="check"></lucide-icon>Trained</span> }</div>
            <div class="big">{{ count(c.key) }}</div>
            <div><b>{{ c.label }}</b><div class="muted small">{{ stat(c.key) }}</div></div>
          </div>
        }
      </div>
      <div class="section-h"><h2>Bids</h2><a class="btn sm primary" routerLink="/bids/new"><lucide-icon name="plus"></lucide-icon>New bid from RFP</a></div>
      <div class="card" style="padding:4px 8px"><div class="tbl-wrap"><table class="tbl">
        <tr><th>RFP</th><th>Department</th><th>Due</th><th>Pages</th><th>Status</th></tr>
        @for (b of bids(); track b.id) {
          <tr class="click" (click)="open(b)"><td class="t1">{{ b.rfpRef || b.title }}<div class="muted small">{{ b.rfpRef ? b.title : '' }}</div></td>
            <td>{{ b.department || '—' }}</td><td>{{ b.dueAt ? (b.dueAt | date: 'dd MMM yyyy') : '—' }}</td><td>{{ b.outputPageCount || '—' }}</td>
            <td><span [class]="chip(b.status)">{{ label(b.status) }}</span></td></tr>
        } @empty { <tr><td colspan="5" class="muted">No bids yet. Upload an RFP to start.</td></tr> }
      </table></div></div>
    </div>`,
})
export class HomePage {
  private api = inject(Api);
  protected router = inject(Router);
  private currentBid = inject(CurrentBid);
  categories = [
    { key: 'experience', label: 'Experience', icon: 'briefcase' }, { key: 'statutory', label: 'Statutory', icon: 'shield-check' },
    { key: 'hr', label: 'Human resources', icon: 'users' }, { key: 'literature', label: 'Bid literature', icon: 'book-open' },
  ];
  bids = signal<BidSummary[]>([]);
  docs = signal<LibraryDoc[]>([]);
  current = this.currentBid.bid;
  hasDraft = this.currentBid.hasDraft;
  label = (s: string) => STATUS_LABEL[s] ?? s;
  chip = chipClass;

  constructor() {
    forkJoin({ bids: this.api.bids(), docs: this.api.library() }).subscribe(r => { this.bids.set(r.bids); this.docs.set(r.docs); });
  }

  /** Experience counts projects; a project's certificates, evidence and RFP are linked to it by work_order_id. */
  private listed(cat: string): LibraryDoc[] { return this.docs().filter(d => d.category === cat && !parentOf(d)); }
  count(cat: string): number { return this.listed(cat).length; }
  processing(cat: string): boolean { return this.docs().some(d => d.category === cat && (d.status === 'uploaded' || d.status === 'processing')); }
  stat(cat: string): string {
    const list = this.listed(cat);
    if (cat === 'experience') {
      const total = list.reduce((a, d) => a + (Number(d.metadata['value_cr']) || 0), 0);
      return total ? `₹ ${total.toFixed(2)} Cr of work on record` : 'Projects with their purchase orders and certificates';
    }
    if (cat === 'statutory') {
      const n = list.filter(d => d.validUntil && new Date(d.validUntil).getTime() - Date.now() < 120 * 86_400_000).length;
      return `${n} certificate${n === 1 ? '' : 's'} expiring soon`;
    }
    if (cat === 'hr') return `${list.filter(d => d.docType === 'cv').length} CVs, ${new Set(list.map(d => d.metadata['role']).filter(Boolean)).size} roles`;
    return `${list.length} reusable write-up${list.length === 1 ? '' : 's'}`;
  }
  open(b: BidSummary): void { this.currentBid.set(b, b.status !== 'new' && b.status !== 'analysing' && b.status !== 'analysed'); this.router.navigate(['/bids', b.id]); }
}
