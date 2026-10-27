import { DatePipe, NgTemplateOutlet } from '@angular/common';
import { Component, DestroyRef, computed, inject, input, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { EMPTY, catchError, switchMap, timer } from 'rxjs';
import { Api, errorText, openBlob } from '../core/api';
import { CurrentBid } from '../core/current-bid';
import { dueIn } from './shared';
import { Icons } from '../core/icons';
import { BidDetail, RFP_DOC_KINDS, RFP_FACTS, RfpFile, guessRfpKind } from '../core/models';

const STEPS = [['Reading the tender documents', ''], ['Finding the bid data and key dates', ''], ['Reading the eligibility criteria', ''],
  ['Reading the evaluation method', ''], ['Listing key personnel and formats', ''], ['Applying corrigenda and addenda', '']];
type Tab = 'pq' | 'tech' | 'hr' | 'forms' | 'docs';

/** Stage 1 of a bid: what the tender asks for, read from all its documents. The bid response is stage 2. */
@Component({
  selector: 'bc-bid-analysis',
  imports: [DatePipe, FormsModule, Icons, NgTemplateOutlet, RouterLink],
  template: `
    <div class="wrap">
      @if (detail(); as d) {
        @if (running()) {
          <div style="max-width:640px"><h1 class="v-title">Reading {{ fileNames() }}</h1><p class="sub">This takes a few minutes for a full tender. You can keep working in the library meanwhile.</p>
            <div class="card" style="margin-top:18px"><div class="bar"><i [style.width.%]="progress()"></i></div>
              <ul class="steps">@for (s of steps; track s[0]; let i = $index) {
                <li [class.done]="i < stepIndex()" [class.run]="i === stepIndex()"><span class="dot">@if (i < stepIndex()) { <lucide-icon name="check"></lucide-icon> } @else if (i === stepIndex()) { <span class="spin" style="width:10px;height:10px"></span> }</span><span>{{ s[0] }}</span></li> }</ul></div></div>
        } @else {
          @if (activeJob(); as j) {
            <div class="notice"><lucide-icon name="info"></lucide-icon><div>{{ jobLabel(j.type) }}… this page updates by itself.</div></div>
          }
          @if (d.bid.status === 'failed') { <div class="notice warn"><lucide-icon name="flag"></lucide-icon><div>{{ d.bid.error }}</div></div> }
          @if (message()) { <div class="notice" [class.warn]="messageWarn()"><lucide-icon name="info"></lucide-icon><div>{{ message() }}</div></div> }

          <div class="card">
            <div class="row between wrapx">
              <div class="row wrapx"><span class="chip blue">RFP {{ meta()['rfp_ref'] || d.bid.rfpRef || 'reference pending' }}</span>
                @if (dueIn(); as due) { <span [class]="due.cls">{{ due.text }}</span> }
                <span class="chip">{{ d.rfpFiles.length }} document{{ d.rfpFiles.length === 1 ? '' : 's' }}</span></div>
              <div class="row wrapx">
                <a class="btn sm quiet" routerLink="/bids">All RFPs</a>
                @if (!editing()) { <button class="btn sm" (click)="startEdit()" [disabled]="!d.analysis.extracted"><lucide-icon name="pencil"></lucide-icon>Edit facts</button> }
                <button class="btn sm" (click)="reanalyse()" [disabled]="busy() || !!activeJob()"><lucide-icon name="rotate-ccw"></lucide-icon>Analyse again</button>
                <a class="btn sm primary" [routerLink]="['/bids', d.bid.id, 'response']" [class.disabled]="!d.analysis.extracted"><lucide-icon name="clipboard-check"></lucide-icon>Prepare bid response</a>
              </div>
            </div>
            @if (editing()) {
              <div class="field" style="margin-top:12px"><label>Project title</label><input class="inp" [(ngModel)]="draft['title']"></div>
              <div class="row wrapx" style="align-items:flex-start">
                <div class="field grow"><label>Department</label><input class="inp" [(ngModel)]="draft['department']"></div>
                <div class="field grow"><label>RFP reference</label><input class="inp" [(ngModel)]="draft['rfp_ref']"></div>
              </div>
              <div class="field"><label>Scope summary</label><textarea class="inp" rows="3" [(ngModel)]="draft['scope_summary']"></textarea></div>
            } @else {
              <h2 style="font-size:19px;margin:12px 0 2px;line-height:1.3">{{ meta()['title'] || d.bid.title }}</h2>
              <div class="muted">{{ meta()['department'] || d.bid.department || '' }}</div>
              @if (meta()['scope_summary']) { <p style="margin:10px 0 0">{{ meta()['scope_summary'] }}</p> }
            }
          </div>

          @if (d.analysis.extracted) {
            <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px;margin-top:14px">
              @for (g of facts; track g.group) {
                <div class="card"><h3>{{ g.group }}</h3>
                  @for (f of g.fields; track f[0]) {
                    @if (editing()) { <div class="field" style="margin:8px 0 0"><label>{{ f[1] }}</label><input class="inp" [(ngModel)]="draft[f[0]]"></div> }
                    @else { <div class="kv"><span>{{ f[1] }}</span><b>{{ meta()[f[0]] || '—' }}@if (edited()[f[0]] !== undefined) { <span class="chip" title="Corrected on this screen; kept when the RFP is analysed again" style="margin-left:6px">edited</span> }</b></div> }
                  }
                </div>
              }
            </div>
            @if (editing()) {
              <div class="row" style="margin-top:12px"><button class="btn" (click)="editing.set(false)">Cancel</button><button class="btn primary" (click)="saveFacts()" [disabled]="busy()">Save facts</button></div>
            }

            @if (amendments().length) {
              <div class="section-h"><h2>Changes made by corrigenda and addenda</h2></div>
              <div class="card" style="padding:4px 8px"><table class="tbl"><tr><th>Document</th><th>What changed</th><th>Change</th></tr>
                @for (a of amendments(); track $index) { <tr><td class="t1">{{ a.document }}</td><td>{{ a.field }}</td><td>{{ a.change }}</td></tr> }</table></div>
            }

            <div class="tabs" style="margin-top:18px">@for (t of tabs; track t[0]) { <button [class.on]="tab() === t[0]" (click)="tab.set(t[0])">{{ t[1] }}<span class="count">{{ count(t[0]) }}</span></button> }</div>
            <div class="card" style="padding:4px 8px"><div class="tbl-wrap">
              @switch (tab()) {
                @case ('pq') { <table class="tbl"><tr><th>Clause</th><th>Criterion</th><th>Documents asked for</th></tr>
                  @for (r of d.analysis.extracted.pq_criteria; track $index) { <tr><td style="white-space:nowrap">{{ r.clause }}</td><td>{{ r.text }}</td><td class="muted">{{ r.evidence_expected }}</td></tr> }
                  @empty { <tr><td colspan="3" class="muted">No pre-qualification criteria were found.</td></tr> }</table> }
                @case ('tech') { <table class="tbl"><tr><th>Clause</th><th>Parameter</th><th>Max marks</th><th>Basis of marking</th></tr>
                  @for (t of d.analysis.extracted.tech_criteria; track $index) { <tr><td style="white-space:nowrap">{{ t.clause || '' }}</td><td class="t1">{{ t.parameter }}</td><td>{{ t.max_marks ?? '—' }}</td><td class="muted">{{ t.basis }}</td></tr> }
                  @empty { <tr><td colspan="4" class="muted">No technical evaluation criteria were found.</td></tr> }
                  @if (techTotal()) { <tr><td></td><td class="t1">Total</td><td><b>{{ techTotal() }}</b></td><td></td></tr> }</table> }
                @case ('hr') { <table class="tbl"><tr><th>Position</th><th>Qualification</th><th>Minimum experience</th><th>Certifications</th></tr>
                  @for (p of d.analysis.extracted.personnel; track $index) { <tr><td class="t1">{{ p.role }}</td><td>{{ p.qualification }}</td><td>{{ p.min_years ? p.min_years + ' years' : '—' }}</td><td>{{ p.certifications.join(', ') || '—' }}</td></tr> }
                  @empty { <tr><td colspan="4" class="muted">No key personnel were found.</td></tr> }</table> }
                @case ('forms') { <table class="tbl"><tr><th>Format</th><th>Description</th></tr>
                  @for (f of d.analysis.extracted.forms; track $index) { <tr><td class="t1">{{ f.code }}</td><td>{{ f.title }}</td></tr> }
                  @empty { <tr><td colspan="2" class="muted">No bid formats were found.</td></tr> }</table> }
                @case ('docs') { <ng-container *ngTemplateOutlet="docsTab"></ng-container> }
              }
            </div></div>
            @if (d.analysis.rfp_pages) {
              <p class="small muted" style="margin-top:12px">{{ d.analysis.rfp_pages }} pages in {{ d.rfpFiles.length }} document(s) indexed on this server;
                {{ d.analysis.sections_sent?.length }} sections sent, redacted, for extraction.@if (d.analysis.analysed_at) { Analysed {{ d.analysis.analysed_at | date: 'dd MMM yyyy, HH:mm' }}. }</p>
            }
          } @else {
            <div class="tabs" style="margin-top:18px"><button class="on">Documents<span class="count">{{ d.rfpFiles.length }}</span></button></div>
            <div class="card" style="padding:4px 8px"><ng-container *ngTemplateOutlet="docsTab"></ng-container></div>
          }

          <ng-template #docsTab>
            <table class="tbl"><tr><th>Document</th><th>Type</th><th>Pages</th><th>Added</th><th></th></tr>
              @for (f of d.rfpFiles; track f.path) {
                <tr><td class="t1">{{ f.name }}</td><td>{{ kindLabel(f.kind) }}</td><td>{{ pagesOf(f) ?? '—' }}</td><td style="white-space:nowrap">{{ f.uploaded_at ? (f.uploaded_at | date: 'dd MMM yyyy') : '—' }}</td>
                  <td style="text-align:right;white-space:nowrap">
                    <button class="ib" (click)="viewDoc(f)" title="View" aria-label="View"><lucide-icon name="eye"></lucide-icon></button>
                    <button class="ib" style="color:var(--bad)" (click)="removeDoc(f)" [disabled]="d.rfpFiles.length === 1 || busy()" title="Remove from this RFP" aria-label="Remove"><lucide-icon name="trash-2"></lucide-icon></button></td></tr>
              }
            </table>
            <div style="padding:10px 6px">
              @for (p of adding(); track $index) {
                <div class="row" style="margin-bottom:6px"><span class="grow">{{ p.file.name }}</span>
                  <select class="inp" style="width:240px" [(ngModel)]="p.kind">@for (k of kinds; track k[0]) { <option [value]="k[0]">{{ k[1] }}</option> }</select>
                  <button class="ib" (click)="dropAdding($index)" aria-label="Remove"><lucide-icon name="x"></lucide-icon></button></div>
              }
              <div class="row wrapx">
                <button class="btn sm" (click)="addPicker.click()"><lucide-icon name="file-plus"></lucide-icon>Add documents</button>
                <input #addPicker type="file" multiple hidden accept=".pdf,.doc,.docx" (change)="pickAdding(addPicker)">
                @if (adding().length) { <button class="btn sm primary" (click)="uploadAdding()" [disabled]="busy()">Upload and analyse again</button> }
                <span class="small muted">Add a corrigendum, addendum or clarification when it is issued; the facts are updated from it.</span>
              </div>
            </div>
          </ng-template>
        }
      } @else { <p class="muted">Loading…</p> }
    </div>`,
})
export class BidAnalysisPage {
  private api = inject(Api);
  private current = inject(CurrentBid);
  readonly id = input.required<string>();
  detail = signal<BidDetail | null>(null);
  tab = signal<Tab>('pq');
  busy = signal(false);
  editing = signal(false);
  message = signal('');
  messageWarn = signal(false);
  elapsed = signal(0);
  adding = signal<{ file: File; kind: string }[]>([]);
  draft: Record<string, string> = {};
  steps = STEPS;
  facts = RFP_FACTS;
  kinds = Object.entries(RFP_DOC_KINDS);
  tabs: [Tab, string][] = [['pq', 'Pre-qualification (PQ)'], ['tech', 'Technical evaluation (TEC)'], ['hr', 'Key personnel'], ['forms', 'Forms'], ['docs', 'Documents']];
  meta = computed(() => (this.detail()?.analysis.extracted?.meta ?? {}) as Record<string, string | null>);
  edited = computed(() => this.detail()?.analysis.edited ?? {});
  amendments = computed(() => this.detail()?.analysis.extracted?.amendments ?? []);
  techTotal = computed(() => (this.detail()?.analysis.extracted?.tech_criteria ?? []).reduce((a, t) => a + (t.max_marks ?? 0), 0));
  activeJob = computed(() => this.detail()?.jobs.find(j => ['queued', 'running'].includes(j.status)) ?? null);
  running = computed(() => { const d = this.detail(); return !!d && (d.bid.status === 'new' || d.bid.status === 'analysing') && !d.analysis.extracted; });
  fileNames = computed(() => { const f = this.detail()?.rfpFiles ?? []; return f.length > 1 ? `${f.length} tender documents` : f[0]?.name ?? 'the RFP'; });
  stepIndex = computed(() => Math.min(STEPS.length - 1, Math.floor(this.elapsed() / 20)));
  progress = computed(() => Math.min(95, this.stepIndex() / STEPS.length * 100 + (this.elapsed() % 20) * 0.8));
  dueIn = computed(() => {
    const due = this.detail()?.bid.dueAt;
    if (!due) return null;
    const { days, passed } = dueIn(due);
    return passed ? { cls: 'chip', text: 'Bid due date passed' }
      : { cls: days <= 7 ? 'chip bad' : days <= 21 ? 'chip warn' : 'chip ok', text: days === 0 ? 'Due today' : `Due in ${days} day${days === 1 ? '' : 's'}` };
  });
  jobLabel = (t: string) => ({ analyze_rfp: 'Reading the tender documents', match_rfp: 'Checking eligibility', draft_bid: 'Drafting the bid', assemble_pdf: 'Building the PDF' } as Record<string, string>)[t] ?? 'Working';
  kindLabel = (k?: string) => RFP_DOC_KINDS[k ?? 'rfp'] ?? k;
  pagesOf = (f: RfpFile) => this.detail()?.analysis.documents?.find(x => x.path === f.path)?.pages ?? null;

  constructor() {
    const destroy = inject(DestroyRef);
    // every 3 s while something is running, every 30 s otherwise
    let tick = 0;
    timer(0, 3000).pipe(
      switchMap(() => this.active() || tick++ % 10 === 0 ? this.api.bid(this.id()).pipe(catchError(() => EMPTY)) : EMPTY),
      takeUntilDestroyed(destroy),
    ).subscribe(d => {
      this.nudge = Math.max(0, this.nudge - 1);
      this.detail.set(d);
      this.elapsed.update(e => e + 3);
      this.current.set(d.bid, d.sections.length > 0);
    });
  }

  /** A few fast polls after an action, until its job shows up in the bid's jobs. */
  private nudge = 0;
  private active(): boolean {
    const d = this.detail();
    return !d || this.busy() || this.nudge > 0 || ['new', 'analysing', 'drafting', 'assembling'].includes(d.bid.status)
      || d.jobs.some(j => ['queued', 'running'].includes(j.status));
  }

  count(t: Tab): number {
    const x = this.detail()?.analysis.extracted;
    return t === 'docs' ? this.detail()?.rfpFiles.length ?? 0
      : t === 'pq' ? x?.pq_criteria.length ?? 0 : t === 'tech' ? x?.tech_criteria.length ?? 0
      : t === 'hr' ? x?.personnel.length ?? 0 : x?.forms.length ?? 0;
  }

  startEdit(): void {
    const m = this.meta();
    this.draft = Object.fromEntries(['title', 'department', 'rfp_ref', 'scope_summary', ...RFP_FACTS.flatMap(g => g.fields.map(f => f[0]))]
      .map(k => [k, m[k] ?? '']));
    this.editing.set(true);
  }

  saveFacts(): void {
    const m = this.meta();
    const changed = Object.fromEntries(Object.entries(this.draft).filter(([k, v]) => (v ?? '').trim() !== (m[k] ?? '')).map(([k, v]) => [k, v.trim() || null]));
    if (!Object.keys(changed).length) { this.editing.set(false); return; }
    this.busy.set(true);
    this.api.updateRfp(this.id(), changed).subscribe({
      next: r => {
        const d = this.detail();
        if (d) this.detail.set({ ...d, analysis: r.analysis, bid: r.bid });
        this.editing.set(false); this.busy.set(false); this.say('Facts saved. They are kept when the RFP is analysed again.');
      },
      error: e => { this.busy.set(false); this.say(errorText(e), true); },
    });
  }

  reanalyse(): void {
    if (!confirm('Read all documents of this RFP again? Facts you corrected are kept; the eligibility check will need to be run again.')) return;
    this.busy.set(true);
    this.api.reanalyse(this.id()).subscribe({ next: () => { this.nudge = 4; this.busy.set(false); this.say('The RFP is being analysed again.'); }, error: e => { this.busy.set(false); this.say(errorText(e), true); } });
  }

  pickAdding(input: HTMLInputElement): void {
    this.adding.update(a => [...a, ...[...(input.files ?? [])].map(file => ({ file, kind: guessRfpKind(file.name) }))]);
    input.value = '';
  }
  dropAdding(i: number): void { this.adding.update(a => a.filter((_, j) => j !== i)); }
  uploadAdding(): void {
    const a = this.adding();
    this.busy.set(true);
    this.api.addBidDocuments(this.id(), a.map(x => x.file), a.map(x => x.kind)).subscribe({
      next: () => { this.nudge = 4; this.adding.set([]); this.busy.set(false); this.say(`${a.length} document(s) added. The RFP is being analysed again.`); },
      error: e => { this.busy.set(false); this.say(errorText(e), true); },
    });
  }

  viewDoc(f: RfpFile): void { this.api.bidDocumentFile(this.id(), f.path).subscribe(openBlob); }
  removeDoc(f: RfpFile): void {
    if (!confirm(`Remove “${f.name}” from this RFP? The RFP is analysed again without it.`)) return;
    this.busy.set(true);
    this.api.removeBidDocument(this.id(), f.path).subscribe({
      next: () => { this.nudge = 4; this.busy.set(false); this.say(`“${f.name}” removed. The RFP is being analysed again.`); },
      error: e => { this.busy.set(false); this.say(errorText(e), true); },
    });
  }

  private say(text: string, warn = false): void { this.message.set(text); this.messageWarn.set(warn); }
}
