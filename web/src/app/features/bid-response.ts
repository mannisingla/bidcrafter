import { DatePipe } from '@angular/common';
import { Component, DestroyRef, computed, inject, input, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Router, RouterLink } from '@angular/router';
import { EMPTY, catchError, firstValueFrom, switchMap, timer } from 'rxjs';
import { Api, errorText, openBlob } from '../core/api';
import { CurrentBid } from '../core/current-bid';
import { Icons } from '../core/icons';
import {
  BID_DOC_TYPES, BidDetail, BidFolder, CATEGORIES, DOC_TYPE_LABELS, EligibilityDecision, LibraryDoc, PersonResult, PqResult, bidOf,
  duplicateNote,
} from '../core/models';
import { STATUS_LABEL, chipClass } from './shared';

interface Edit {
  kind: 'pq' | 'personnel'; index: number; heading: string; text: string;
  status: string; chosen: string[]; note: string; decided: boolean;
}

/** Tabs of the document picker: the documents uploaded for this bid, the library's categories, with undertakings
 * (signed undertakings, declarations, affidavits) apart from the other statutory documents, and those chosen so far. */
type PickTab = 'selected' | 'bid' | 'experience' | 'statutory' | 'hr' | 'undertakings' | 'literature' | 'uploads';
const UNDERTAKING_TITLE = /\b(?:undertaking|declaration|affidavit|self[- ]?certif)/i;
const isUndertaking = (d: LibraryDoc) => d.docType === 'undertaking' || UNDERTAKING_TITLE.test(d.title);
function inTab(d: LibraryDoc, tab: PickTab, chosen: string[]): boolean {
  if (tab === 'selected') return chosen.includes(d.id);
  if (tab === 'bid') return !!bidOf(d);
  if (tab === 'undertakings') return isUndertaking(d);
  return d.category === tab && !isUndertaking(d);
}
/** Where a criterion's documents most likely are, by its category; statutory otherwise. */
const CRITERION_TAB: Partial<Record<string, PickTab>> = {
  project_experience: 'experience', domain_experience: 'experience', manpower: 'hr',
  blacklisting: 'undertakings', local_presence: 'undertakings',
};

/** Stage 2 of a bid: the RFP's criteria checked against the library, then the bid draft. Stage 1 is the RFP analysis. */
@Component({
  selector: 'bc-bid-response',
  imports: [DatePipe, Icons, RouterLink],
  template: `
    <div class="wrap">
      @if (detail(); as d) {
        <a class="small" [routerLink]="['/bids', d.bid.id]"><lucide-icon name="chevron-left"></lucide-icon>RFP analysis</a>
        <h1 class="v-title" style="margin-top:6px">Bid response</h1>
        <p class="sub">{{ d.analysis.extracted?.meta?.['title'] || d.bid.title }}{{ d.bid.dueAt ? ' · bid due ' + (d.bid.dueAt | date: 'dd MMM yyyy') : '' }}</p>
        <p class="small muted" style="margin:-6px 0 14px">What the bid submits: each pre-qualification criterion and key position checked against your library,
          with the documents (or CV) that answer it. Confirm or change them here; the bid draft follows. The wording, order and extra pages
          of the bid are edited in the Bid builder.</p>
        <div class="row wrapx small" style="gap:14px;margin:-4px 0 14px">
          <a href="" (click)="$event.preventDefault(); jump('bid-docs')"><lucide-icon name="folder" style="width:14px;height:14px;vertical-align:-2px"></lucide-icon> Documents for this bid ({{ bidDocs().length }})</a>
          <a href="" (click)="$event.preventDefault(); jump('bid-folder')"><lucide-icon name="download" style="width:14px;height:14px;vertical-align:-2px"></lucide-icon> Bid folder (built PDFs)</a>
        </div>

        @if (activeJob(); as j) {
          <div class="notice"><lucide-icon name="info"></lucide-icon><div><span class="spin"></span> {{ jobLabel(j.type) }}… this page updates by itself.</div></div>
        }
        @if (failedJob(); as j) { <div class="notice warn"><lucide-icon name="flag"></lucide-icon><div>{{ jobLabel(j.type) }} failed: {{ j.error }}</div></div> }
        @if (d.bid.status === 'failed') { <div class="notice warn"><lucide-icon name="flag"></lucide-icon><div>{{ d.bid.error }}</div></div> }
        @if (error()) { <div class="notice warn"><lucide-icon name="flag"></lucide-icon><div>{{ error() }}</div></div> }

        @if (!d.analysis.extracted) {
          <div class="notice"><lucide-icon name="info"></lucide-icon><div>The RFP has not been analysed yet. <a [routerLink]="['/bids', d.bid.id]">Open the RFP analysis</a>.</div></div>
        } @else if (!d.analysis.match) {
          <div class="card" style="max-width:720px">
            <h3>Check eligibility against your library</h3>
            <p class="muted small">Each pre-qualification criterion and key-personnel requirement of the RFP is matched against your
              Experience, Statutory and HR documents on this server, with plain rules. Nothing is sent outside.</p>
            <button class="btn primary" (click)="check()" [disabled]="busy() || !!activeJob()"><lucide-icon name="clipboard-check"></lucide-icon>Check eligibility</button>
          </div>
        } @else {
          @if (d.analysis.match; as m) {
            <div class="rfp-head">
              <div class="card">
                <div class="row between wrapx"><h3 style="margin:0">Eligibility</h3>
                  <div class="row wrapx">
                    <button class="btn sm" (click)="check()" [disabled]="busy() || !!activeJob()" title="Run the check again, e.g. after adding documents to the library"><lucide-icon name="rotate-ccw"></lucide-icon>Check again</button>
                    @if (d.sections.length) { <a class="btn sm primary" [routerLink]="['/bids', d.bid.id, 'builder']"><lucide-icon name="layers"></lucide-icon>Open bid draft</a><button class="btn sm quiet" (click)="draft(true)" [disabled]="busy() || !!activeJob()"><lucide-icon name="sparkles"></lucide-icon>Redraft</button> }
                    @else { <button class="btn sm primary" (click)="draft(false)" [disabled]="busy() || !!activeJob()"><lucide-icon name="layers"></lucide-icon>Generate bid draft</button> }
                  </div></div>
                <p class="small muted" style="margin:8px 0 0">{{ m.pq.length }} pre-qualification criteria and {{ m.personnel.length }} key-personnel requirements checked
                  @if (d.analysis.matched_at) { on {{ d.analysis.matched_at | date: 'dd MMM yyyy, HH:mm' }} }; reference date {{ m.reference_date }}.</p>
              </div>
              <div class="card ring">
                <svg viewBox="0 0 120 120" width="130" height="130" aria-hidden="true"><circle cx="60" cy="60" r="50" fill="none" stroke="var(--hover)" stroke-width="12"/><circle cx="60" cy="60" r="50" fill="none" stroke="var(--ok)" stroke-width="12" [attr.stroke-dasharray]="(met() / total() * 314) + ' 314'" transform="rotate(-90 60 60)"/><text x="60" y="66" text-anchor="middle" font-size="22" font-weight="700" fill="currentColor">{{ met() }}/{{ total() }}</text></svg>
                <div class="row wrapx" style="justify-content:center">@for (s of summary(); track s[0]) { @if (s[0] !== 'met') { <span [class]="chip(s[0])">{{ s[1] }} {{ label(s[0]).toLowerCase() }}</span> } }</div>
              </div>
            </div>
            <div class="tabs">@for (t of tabs; track t[0]) { <button [class.on]="tab() === t[0]" (click)="tab.set(t[0])">{{ t[1] }}</button> }</div>
            <div class="card" style="padding:4px 8px"><div class="tbl-wrap">
              @switch (tab()) {
                @case ('pq') { <table class="tbl"><tr><th>Clause</th><th>Criterion</th><th>Evidence from library</th><th>Result</th></tr>
                  @for (r of m.pq; track $index) { <tr><td>{{ r.clause }}</td><td>{{ r.text }}<div class="muted small">{{ r.note }}</div></td>
                    <td>@if (r.evidence.length) { <div class="ev">@for (e of r.evidence; track e) { <a href="" (click)="$event.preventDefault(); openDoc(e)">{{ docTitle(e) }}</a> }</div> } @else if (r.status === 'undertaking') { <span class="muted small">Undertaking drafted with the bid</span> } @else { <span class="muted small">—</span> }</td>
                    <td style="white-space:nowrap"><span [class]="chip(r.status)">@if (r.status === 'met') { <lucide-icon name="check"></lucide-icon> } @else if (r.status === 'gap') { <lucide-icon name="flag"></lucide-icon> }{{ label(r.status) }}</span>
                      @if (r.decided) { <div class="muted small" [title]="'Automatic result: ' + label(r.auto?.status ?? '')">By {{ who(r.decided.by) }}, {{ r.decided.at | date: 'dd MMM' }}</div> }
                      <div class="row" style="gap:4px;margin-top:6px">
                        @if (!r.decided && (r.status === 'review' || r.status === 'partial') && r.evidence.length) { <button class="btn sm" (click)="confirmPq($index, r)" [disabled]="saving()" title="The documents shown meet this criterion"><lucide-icon name="check"></lucide-icon>Confirm</button> }
                        <button class="ib" (click)="editPq($index, r)" [disabled]="saving()" title="Change the result or the documents" aria-label="Change the result or the documents"><lucide-icon name="pencil"></lucide-icon></button>
                      </div></td></tr> }</table> }
                @case ('hr') { <table class="tbl"><tr><th>Position</th><th>RFP requirement</th><th>Matched from library</th><th>Result</th></tr>
                  @for (p of m.personnel; track $index) { <tr><td class="t1">{{ p.role }}</td><td>{{ p.qualification }}@if (p.min_years) {; {{ p.min_years }}+ years}</td>
                    <td>@if (p.document_id) { <div class="ev"><a href="" (click)="$event.preventDefault(); openDoc(p.document_id)">{{ p.name || 'CV' }}</a></div><div class="muted small">{{ p.years ? p.years + ' yrs' : '' }}</div> } @else { — }</td>
                    <td><span [class]="chip(p.status)">{{ label(p.status) }}</span><div class="muted small">{{ p.note }}</div>
                      @if (p.decided) { <div class="muted small" [title]="'Automatic result: ' + label(p.auto?.status ?? '') + (p.auto?.name ? ', ' + p.auto?.name : '')">By {{ who(p.decided.by) }}, {{ p.decided.at | date: 'dd MMM' }}</div> }
                      <div class="row" style="gap:4px;margin-top:6px">
                        @if (!p.decided && p.status === 'partial' && p.document_id) { <button class="btn sm" (click)="confirmPerson($index, p)" [disabled]="saving()" title="This CV meets the requirement"><lucide-icon name="check"></lucide-icon>Confirm</button> }
                        <button class="ib" (click)="editPerson($index, p)" [disabled]="saving()" title="Change the result or the CV" aria-label="Change the result or the CV"><lucide-icon name="pencil"></lucide-icon></button>
                      </div></td></tr> }</table> }
              }
            </div></div>
            @if (saved()) { <div class="notice" style="margin-top:10px"><lucide-icon name="info"></lucide-icon><div>{{ saved() }}</div></div> }
          }
        }

        @if (editing(); as e) {
          <div class="modal-bg open" (click)="editing.set(null)">
            <div class="modal" (click)="$event.stopPropagation()">
              <div class="modal-h"><div><h3>{{ e.heading }}</h3><div class="muted small">{{ e.text }}</div></div>
                <button class="ib" (click)="editing.set(null)" aria-label="Close"><lucide-icon name="x"></lucide-icon></button></div>
              <div class="modal-b" style="padding-top:10px">
                @if (error()) { <div class="notice warn"><lucide-icon name="flag"></lucide-icon><div>{{ error() }}</div></div> }
                <div class="field"><label>Result</label>
                  <div class="row wrapx" style="gap:6px">@for (s of e.kind === 'pq' ? pqResults : personResults; track s) {
                    <button class="btn sm" [class.primary]="e.status === s" (click)="patch({ status: s })">{{ label(s) }}</button> }</div></div>
                <div class="field"><label>{{ e.kind === 'pq' ? 'Documents enclosed for this criterion (' + e.chosen.length + ' chosen)' : 'CV proposed' }}</label>
                  <input class="inp" placeholder="Search the library: name, client, type, number" [value]="pickQ()" (input)="pickQ.set($any($event.target).value)"></div>
                @if (e.kind === 'pq') {
                  <div class="tabs" style="margin-bottom:4px">@for (t of pickTabs; track t[0]) {
                    <button [class.on]="pickTab() === t[0]" (click)="pickTab.set(t[0])">{{ t[1] }}<span class="count">{{ counts()[t[0]] }}</span></button> }</div>
                }
                @if (e.kind === 'personnel') {
                  <label class="pick"><input type="radio" name="cv" [checked]="!e.chosen.length" (change)="patch({ chosen: [] })"><span style="flex:1" class="muted">No CV</span></label>
                }
                @for (doc of candidates(); track doc.id) {
                  <label class="pick"><input [type]="e.kind === 'pq' ? 'checkbox' : 'radio'" name="cv" [checked]="e.chosen.includes(doc.id)" (change)="toggle(doc.id)">
                    <span style="flex:1"><b>{{ docName(doc) }}</b><div class="muted small">{{ catLabel(doc.category) }} · {{ typeLabel(doc) }}@if (doc.validUntil) { · valid till {{ doc.validUntil | date: 'dd MMM yyyy' }} }</div></span>
                    <a href="" class="small" (click)="$event.preventDefault(); $event.stopPropagation(); openDoc(doc.id)">View</a></label>
                } @empty {
                  <p class="muted" style="padding:10px 6px">
                    @if (pickQ()) { No documents here match the search. }
                    @else if (e.kind === 'pq' && pickTab() === 'selected') { Nothing chosen yet: pick documents from the other tabs. }
                    @else if (e.kind === 'pq' && pickTab() === 'undertakings') { No signed undertakings or declarations in the library yet. Upload them to Statutory documents as type “Undertaking / Declaration”. }
                    @else { Nothing here yet. }</p>
                }
                <div class="field" style="margin-top:14px"><label>Note (optional)</label>
                  <input class="inp" maxlength="500" placeholder="For example: CA certificate for FY 2023-24 to be added" [value]="e.note" (input)="patch({ note: $any($event.target).value })"></div>
              </div>
              <div class="modal-f">
                @if (e.decided) { <button class="btn quiet" style="margin-right:auto" (click)="reset()" [disabled]="saving()" title="Use the result of the eligibility check again"><lucide-icon name="rotate-ccw"></lucide-icon>Back to automatic result</button> }
                <button class="btn" (click)="editing.set(null)">Cancel</button>
                <button class="btn primary" (click)="saveEdit()" [disabled]="saving()">{{ saving() ? 'Saving…' : 'Save' }}</button></div>
            </div>
          </div>
        }

        <div class="section-h" id="bid-docs"><h2>Documents for this bid</h2>
          <div class="row wrapx" style="gap:6px">
            <select class="inp" style="width:auto" (change)="upType.set($any($event.target).value)" aria-label="Type of the documents to upload">
              @for (t of bidTypes; track t[0]) { <option [value]="t[0]" [selected]="upType() === t[0]">{{ t[1] }}</option> }</select>
            <button class="btn sm primary" (click)="bidPicker.click()" [disabled]="uploading()"><lucide-icon name="upload"></lucide-icon>{{ uploading() ? 'Uploading…' : 'Upload' }}</button>
            <input #bidPicker type="file" multiple hidden accept=".pdf,.doc,.docx,.jpg,.jpeg,.png,.tif,.tiff" (change)="uploadBidDocs(bidPicker)">
          </div></div>
        <div class="card" style="padding:4px 8px">
          <p class="small muted" style="margin:8px 8px 4px">Documents that belong to this bid only: EMD / bank guarantee, tender fee receipt, power of attorney or
            board resolution, signed forms, integrity pact{{ d.analysis.extracted?.meta?.['emd'] ? ' (EMD asked for: ' + d.analysis.extracted?.meta?.['emd'] + ')' : '' }}.
            They stay out of the library. Attach each where it belongs: below, in the Bid builder, or against a pre-qualification criterion above (tab “This bid”).
            A new draft puts the EMD and tender fee after the covering letter and uses this bid's power of attorney.</p>
          @if (bidMessage()) { <div class="notice" style="margin:6px 8px"><lucide-icon name="info"></lucide-icon><div>{{ bidMessage() }}</div></div> }
          @if (bidDocs().length) {
            <div class="tbl-wrap"><table class="tbl"><tr><th>Document</th><th>Type</th><th>Where in the bid</th><th></th></tr>
              @for (doc of bidDocs(); track doc.id) {
                <tr><td class="t1">{{ doc.title }}<div class="muted small">{{ doc.originalName }}{{ doc.fileSize ? ' · ' + size(doc.fileSize) : '' }}</div>
                    @if (doc.status === 'failed') { <span class="chip bad" [title]="doc.error || ''">Could not read</span> } @else if (doc.status !== 'ready') { <span class="chip blue"><span class="spin"></span>Reading</span> }</td>
                  <td><select class="inp" style="width:auto" (change)="setBidDocType(doc, $any($event.target).value)" [attr.aria-label]="'Type of ' + doc.title">
                    @for (t of bidTypes; track t[0]) { <option [value]="t[0]" [selected]="doc.docType === t[0]">{{ t[1] }}</option> }</select></td>
                  <td>@for (s of placedIn(doc.id); track $index) { <div class="small">{{ s }}</div> } @empty { <span class="muted small">Not in the bid yet</span> }
                    @if (d.sections.length && doc.status === 'ready') {
                      <select class="inp" style="width:auto;margin-top:4px;display:block" (change)="attachBidDoc(doc, $any($event.target))" [attr.aria-label]="'Attach ' + doc.title">
                        <option value="">Attach to…</option>
                        @for (s of docSections(); track s.id) { <option [value]="s.id">{{ s.title }}</option> }
                        <option value="new">A new annexure at the end</option></select>
                    }</td>
                  <td style="text-align:right;white-space:nowrap">
                    <button class="ib" (click)="openDoc(doc.id)" [disabled]="doc.status !== 'ready'" title="View" aria-label="View"><lucide-icon name="eye"></lucide-icon></button>
                    <button class="ib" (click)="downloadDoc(doc)" title="Download the file as uploaded" aria-label="Download"><lucide-icon name="download"></lucide-icon></button>
                    <button class="ib" style="color:var(--bad)" (click)="removeBidDoc(doc)" title="Delete" aria-label="Delete"><lucide-icon name="trash-2"></lucide-icon></button></td></tr>
              }
            </table></div>
          }
        </div>

        @if (d.analysis.extracted) {
          <div class="section-h" id="bid-folder"><h2>Bid folder</h2>
            @if (folder()?.files?.length) { <button class="btn sm primary" (click)="downloadZip()" [disabled]="busy()"><lucide-icon name="download"></lucide-icon>Download all (ZIP{{ folder()?.zip_size ? ', ' + size(folder()!.zip_size!) : '' }})</button> }
          </div>
          <div class="card" style="padding:4px 8px">
            @if (folder()?.files?.length) {
              <p class="small muted" style="margin:8px 8px 4px">{{ folder()!.folder }} — prepared {{ folder()!.built_at | date: 'dd MMM yyyy, HH:mm' }} with the PDF. Each part is a separate PDF for portals that ask for separate uploads; page numbers and stamps match the complete bid.</p>
              <div class="tbl-wrap"><table class="tbl"><tr><th>File</th><th>Pages</th><th>Size</th><th></th></tr>
                @for (f of folder()!.files; track f.name) {
                  <tr><td [class.t1]="f.kind === 'complete'"><lucide-icon [name]="f.kind === 'rfp' ? 'file-search' : 'file-text'" style="width:15px;height:15px;vertical-align:-2px;margin-right:6px"></lucide-icon>{{ f.name }}</td>
                    <td>{{ f.pages ?? '—' }}</td><td style="white-space:nowrap">{{ size(f.size) }}</td>
                    <td style="text-align:right;white-space:nowrap">
                      @if (f.name.toLowerCase().endsWith('.pdf')) { <button class="ib" (click)="viewFile(f.name)" title="View" aria-label="View"><lucide-icon name="eye"></lucide-icon></button> }
                      <button class="ib" (click)="downloadFile(f.name)" title="Download" aria-label="Download"><lucide-icon name="download"></lucide-icon></button></td></tr>
                }
              </table></div>
            } @else {
              <p class="muted small" style="padding:10px 12px">The bid folder is prepared when the PDF is built: generate the bid draft, then use
                <a [routerLink]="['/bids', d.bid.id, 'preview']">Preview &amp; sign</a> → <i>Save &amp; build PDF</i>. It holds the complete signed bid,
                each section and each annexure document (ROC, PAN, GST, certifications…) as its own PDF, and the RFP documents.</p>
            }
          </div>
        }
      } @else { <p class="muted">Loading…</p> }
    </div>`,
})
export class BidResponsePage {
  private api = inject(Api);
  private router = inject(Router);
  private current = inject(CurrentBid);
  readonly id = input.required<string>();
  detail = signal<BidDetail | null>(null);
  folder = signal<BidFolder | null>(null);
  tab = signal<'pq' | 'hr'>('pq');
  busy = signal(false);
  error = signal('');
  tabs: ['pq' | 'hr', string][] = [['pq', 'Pre-qualification'], ['hr', 'Key personnel']];
  label = (s: string) => STATUS_LABEL[s] ?? s;
  chip = chipClass;
  activeJob = computed(() => this.detail()?.jobs.find(j => ['queued', 'running'].includes(j.status)) ?? null);
  /** The latest eligibility or drafting job, if it failed and nothing has run since. */
  failedJob = computed(() => { const j = this.detail()?.jobs.find(x => ['match_rfp', 'draft_bid', 'sync_draft'].includes(x.type)); return j?.status === 'failed' ? j : null; });
  summary = computed(() => Object.entries(this.detail()?.analysis.match?.summary ?? {}));
  total = computed(() => this.detail()?.analysis.match?.pq.length || 1);
  met = computed(() => (this.detail()?.analysis.match?.pq ?? []).filter(r => r.status === 'met' || r.status === 'undertaking').length);
  jobLabel = (t: string) => ({ analyze_rfp: 'Reading the tender documents', match_rfp: 'Checking eligibility', draft_bid: 'Drafting the bid', sync_draft: 'Updating the bid draft with your choice', assemble_pdf: 'Building the PDF' } as Record<string, string>)[t] ?? 'Working';
  private titles = new Map<string, string>();

  constructor() {
    const destroy = inject(DestroyRef);
    let last = '';
    // every 3 s while something is running, every 30 s otherwise
    let tick = 0;
    timer(0, 3000).pipe(
      switchMap(() => this.active() || tick++ % 10 === 0 ? this.api.bid(this.id()).pipe(catchError(() => EMPTY)) : EMPTY),
      takeUntilDestroyed(destroy),
    ).subscribe(d => {
      this.nudge = Math.max(0, this.nudge - 1);
      const prev = last; last = d.bid.status;
      if (prev !== 'ready' && d.bid.status === 'ready' || !this.folder()) this.loadFolder(d.bid.id);
      this.detail.set(d);
      this.current.set(d.bid, d.sections.length > 0);
      if (!this.bidDocsLoaded || this.bidDocs().some(x => x.status === 'uploaded' || x.status === 'processing')) {
        this.bidDocsLoaded = true;
        this.loadBidDocs();
      }
      if (prev === 'drafting' && d.bid.status === 'drafted') this.router.navigate(['/bids', d.bid.id, 'builder']);
    });
    this.api.library().subscribe(list => { this.remember(list); this.library.set(list); });
  }

  private remember(list: LibraryDoc[]): void {
    for (const x of list) {
      const fs = x.docType === 'financial_statement' ? [x.metadata['financial_year'] && `FY ${x.metadata['financial_year']}`, x.metadata['version']].filter(Boolean) : [];
      this.titles.set(x.id, fs.length ? `${x.title} (${fs.join(', ')})` : x.title);
    }
  }

  // ---- documents uploaded for this bid only (EMD, power of attorney, signed forms…)
  bidTypes = BID_DOC_TYPES;
  bidDocs = signal<LibraryDoc[]>([]);
  private bidDocsLoaded = false;
  upType = signal('emd');
  uploading = signal(false);
  bidMessage = signal('');
  docSections = computed(() => (this.detail()?.sections ?? []).filter(s => s.kind === 'docs'));
  placedIn(id: string): string[] { return this.docSections().filter(s => s.documents.some(x => x.id === id)).map(s => s.title); }
  private loadBidDocs(): void { this.api.bidDocuments(this.id()).subscribe(list => { this.remember(list); this.bidDocs.set(list); }); }
  private reloadBid(): void { this.api.bid(this.id()).subscribe(d => this.detail.set(d)); }
  uploadBidDocs(input: HTMLInputElement): void {
    const files = [...(input.files ?? [])];
    input.value = '';
    if (!files.length) return;
    this.uploading.set(true); this.bidMessage.set('');
    this.api.upload(files, 'uploads', 'internal', { bidId: this.id(), docType: this.upType() }).subscribe({
      next: r => { this.uploading.set(false); this.bidMessage.set(duplicateNote(r)); this.loadBidDocs(); this.nudge = 4; },
      error: e => { this.uploading.set(false); this.bidMessage.set(errorText(e)); },
    });
  }
  setBidDocType(doc: LibraryDoc, docType: string): void {
    this.api.updateDoc(doc.id, { title: doc.title, category: doc.category, docType, sensitivity: doc.sensitivity, validUntil: doc.validUntil, metadata: doc.metadata })
      .subscribe({ next: () => this.loadBidDocs(), error: e => this.bidMessage.set(errorText(e)) });
  }
  /** Into an annexure section of the draft, or a new annexure at its end. */
  async attachBidDoc(doc: LibraryDoc, select: HTMLSelectElement): Promise<void> {
    const target = select.value;
    select.value = '';
    if (!target) return;
    try {
      const section = this.docSections().find(s => s.id === target);
      if (section?.documents.some(x => x.id === doc.id)) { this.bidMessage.set(`Already in “${section.title}”.`); return; }
      if (section) {
        await firstValueFrom(this.api.setSectionDocs(this.id(), section.id, [...section.documents.map(x => x.id), doc.id]));
      } else {
        const all = this.detail()?.sections ?? [];
        const created = await firstValueFrom(this.api.addSection(this.id(), 'docs', `Annexure: ${DOC_TYPE_LABELS[doc.docType] ?? doc.title}`, all[all.length - 1]?.id));
        await firstValueFrom(this.api.setSectionDocs(this.id(), created.id, [doc.id]));
      }
      this.bidMessage.set(`“${doc.title}” attached${section ? ` to “${section.title}”` : ' as a new annexure at the end'}. Build the PDF again in Preview & sign to include it.`);
      this.reloadBid();
    } catch (e) { this.bidMessage.set(errorText(e)); }
  }
  removeBidDoc(doc: LibraryDoc): void {
    if (!confirm(`Delete “${doc.title}”? It is also taken out of the bid draft.`)) return;
    this.api.deleteDoc(doc.id).subscribe({ next: () => { this.loadBidDocs(); this.reloadBid(); }, error: e => this.bidMessage.set(errorText(e)) });
  }

  size = (bytes: number) => bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  private loadFolder(id: string): void { this.api.bidFolder(id).subscribe({ next: f => this.folder.set(f), error: () => undefined }); }
  viewFile(name: string): void { this.api.bidFolderFile(this.id(), name).subscribe(openBlob); }
  downloadFile(name: string): void { this.api.bidFolderFile(this.id(), name).subscribe(b => this.save(b, name.split('/').pop() || name)); }
  downloadZip(): void {
    this.busy.set(true);
    this.api.bidFolderZip(this.id()).subscribe({
      next: b => { this.busy.set(false); this.save(b, `${this.folder()?.folder ?? 'Bid documents'}.zip`); },
      error: e => { this.busy.set(false); this.error.set(errorText(e)); },
    });
  }
  private save(blob: Blob, name: string): void {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = name; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  }
  /** A few fast polls after an action, until its job shows up in the bid's jobs. */
  private nudge = 0;
  private active(): boolean {
    const d = this.detail();
    return !d || this.busy() || this.nudge > 0 || ['new', 'analysing', 'drafting', 'assembling'].includes(d.bid.status)
      || this.bidDocs().some(x => x.status === 'uploaded' || x.status === 'processing')
      || d.jobs.some(j => ['queued', 'running'].includes(j.status));
  }
  docTitle(id: string): string { return this.titles.get(id) ?? 'Document'; }

  // A person confirms a result, or changes it and the documents (CV) it rests on. Kept when eligibility is checked
  // again; the bid draft encloses the documents chosen here.
  pqResults = ['met', 'partial', 'gap', 'undertaking'];
  personResults = ['met', 'partial', 'gap'];
  editing = signal<Edit | null>(null);
  pickQ = signal('');
  saving = signal(false);
  saved = signal('');
  private library = signal<LibraryDoc[]>([]);
  who = (by: string) => by.split('@')[0];
  catLabel = (c: string) => CATEGORIES.find(x => x.key === c)?.label ?? c;
  /** The type, and for audited financial statements their year and version (short is enclosed unless asked). */
  typeLabel = (d: LibraryDoc) => [DOC_TYPE_LABELS[d.docType] ?? d.docType.replaceAll('_', ' '),
    ...(d.docType === 'financial_statement'
      ? [d.metadata['financial_year'] && `FY ${d.metadata['financial_year']}`, d.metadata['version'] && `${d.metadata['version']} version`] : [])]
    .filter(Boolean).join(' · ');
  docName = (d: LibraryDoc) => String(d.metadata['short_name'] || d.metadata['name'] || d.title);
  pickTabs: [PickTab, string][] = [['selected', 'Selected'], ['bid', 'This bid'], ['experience', 'Experience'], ['statutory', 'Statutory documents'],
    ['hr', 'Resources'], ['undertakings', 'Undertakings'], ['literature', 'Bid literature'], ['uploads', 'Other uploads']];
  pickTab = signal<PickTab>('statutory');
  /** Library documents that can be chosen (CVs for a key position), narrowed by the search. */
  private pool = computed(() => {
    const e = this.editing();
    if (!e) return [];
    const q = this.pickQ().trim().toLowerCase();
    return [...this.bidDocs(), ...this.library()]
      .filter(d => d.status === 'ready' && d.sensitivity !== 'restricted' && (e.kind === 'pq' || (d.category === 'hr' && d.docType === 'cv')))
      .filter(d => !q || [d.title, d.originalName, this.typeLabel(d), ...Object.values(d.metadata).filter(v => typeof v === 'string')]
        .some(v => String(v).toLowerCase().includes(q)));
  });
  counts = computed(() => {
    const chosen = this.editing()?.chosen ?? [];
    return Object.fromEntries(this.pickTabs.map(([t]) => [t, this.pool().filter(d => inTab(d, t, chosen)).length])) as Record<PickTab, number>;
  });
  /** The documents of the open tab: chosen ones in the order chosen, the others by name. */
  candidates = computed(() => {
    const e = this.editing();
    if (!e) return [];
    const byName = (a: LibraryDoc, b: LibraryDoc) => this.docName(a).localeCompare(this.docName(b));
    if (e.kind === 'personnel') return [...this.pool()].sort(byName);
    const tab = this.pickTab();
    const list = this.pool().filter(d => inTab(d, tab, e.chosen));
    return tab === 'selected' ? list.sort((a, b) => e.chosen.indexOf(a.id) - e.chosen.indexOf(b.id)) : list.sort(byName);
  });

  confirmPq(index: number, r: PqResult): void { this.decide('pq', index, { status: 'met', evidence: r.evidence }); }
  confirmPerson(index: number, p: PersonResult): void { this.decide('personnel', index, { status: 'met', documentId: p.document_id }); }
  editPq(index: number, r: PqResult): void {
    this.pickTab.set(r.evidence.length ? 'selected' : CRITERION_TAB[r.category] ?? 'statutory');
    this.open({ kind: 'pq', index, heading: `Clause ${r.clause || index + 1}`, text: r.text, status: r.status === 'review' ? 'met' : r.status,
      chosen: [...r.evidence], note: r.decided ? r.note : '', decided: !!r.decided });
  }
  editPerson(index: number, p: PersonResult): void {
    this.open({ kind: 'personnel', index, heading: p.role, text: `${p.qualification}${p.min_years ? `; ${p.min_years}+ years` : ''}`,
      status: p.status, chosen: p.document_id ? [p.document_id] : [], note: p.decided ? p.note : '', decided: !!p.decided });
  }
  private open(e: Edit): void { this.error.set(''); this.pickQ.set(''); this.editing.set(e); }
  patch(change: Partial<Edit>): void { this.editing.update(e => e ? { ...e, ...change } : e); }
  toggle(id: string): void {
    const e = this.editing();
    if (!e) return;
    this.patch({ chosen: e.kind === 'personnel' ? [id] : e.chosen.includes(id) ? e.chosen.filter(x => x !== id) : [...e.chosen, id] });
  }
  saveEdit(): void {
    const e = this.editing();
    if (!e) return;
    this.decide(e.kind, e.index, e.kind === 'pq'
      ? { status: e.status, evidence: e.chosen, note: e.note }
      : { status: e.status, documentId: e.chosen[0] ?? null, note: e.note });
  }
  reset(): void { const e = this.editing(); if (e) this.decide(e.kind, e.index, { reset: true }); }
  private decide(kind: 'pq' | 'personnel', index: number, decision: EligibilityDecision): void {
    this.saving.set(true); this.error.set('');
    this.api.decideEligibility(this.id(), kind, index, decision).subscribe({
      next: r => {
        this.detail.update(d => d ? { ...d, analysis: r.analysis } : d);
        this.saving.set(false); this.editing.set(null);
        // the worker updates that criterion in the existing draft (its documents and separator page); poll until it is done
        if (r.draftUpdating) this.nudge = 4;
        this.saved.set(r.draftUpdating ? 'Saved, and applied to the bid draft. Build the PDF again in Preview & sign to include it.' : '');
      },
      error: err => { this.error.set(errorText(err)); this.saving.set(false); },
    });
  }
  check(): void {
    this.busy.set(true); this.error.set('');
    this.api.checkEligibility(this.id()).subscribe({ next: () => { this.nudge = 4; this.busy.set(false); }, error: e => { this.error.set(errorText(e)); this.busy.set(false); } });
  }
  draft(overwrite: boolean): void {
    if (overwrite && !confirm('Replace the current draft? Your edits to it will be lost.')) return;
    this.busy.set(true); this.error.set('');
    this.api.draft(this.id(), overwrite).subscribe({ next: () => { this.nudge = 4; this.busy.set(false); }, error: e => { this.error.set(errorText(e)); this.busy.set(false); } });
  }
  openDoc(id: string): void { this.api.docFile(id, true).subscribe(openBlob); }
  /** The file exactly as uploaded, under its own name. */
  downloadDoc(doc: LibraryDoc): void {
    this.api.docFile(doc.id, false).subscribe({ next: b => this.save(b, doc.originalName || doc.title), error: e => this.bidMessage.set(errorText(e)) });
  }
  jump(id: string): void { document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
}
