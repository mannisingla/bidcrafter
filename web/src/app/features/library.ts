import { Component, DestroyRef, OnInit, computed, inject, input, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { NgTemplateOutlet } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { DomSanitizer, SafeResourceUrl } from '@angular/platform-browser';
import { Router } from '@angular/router';
import { EMPTY, catchError, switchMap, timer } from 'rxjs';
import { Api, errorText } from '../core/api';
import { Icons } from '../core/icons';
import {
  CATEGORIES, CERT_STANDARDS, Category, DETAIL_HINTS, DOC_TYPE_LABELS, INDIAN_STATES, LibraryDoc, READING_HINT, Sensitivity, duplicateNote,
  parentOf, poKey, readingLabel, samePersonReason,
} from '../core/models';
import { validity } from './shared';

interface EditForm { title: string; category: Category; docType: string; fields: Record<string, string>; sensitivity: Sensitivity; validUntil: string; details: string; }
type ProjectSort = 'project' | 'po_date' | 'client' | 'value';
/** Details shown as plain fields in the edit panel (the rest stay in the JSON box). */
interface FieldDef { key: string; label: string; kind?: 'number' | 'list'; options?: string[]; select?: boolean; placeholder?: string; }
const STATUTORY_FIELDS: FieldDef[] = [{ key: 'number', label: 'Document number', placeholder: 'Certificate, registration or reference number' }];
const TYPE_FIELDS: Record<string, FieldDef[]> = {
  certification: [
    { key: 'standard', label: 'Standard', options: CERT_STANDARDS, placeholder: 'ISO 9001:2015, CMMI…' },
    { key: 'level', label: 'Level (CMMI)', kind: 'number' },
  ],
  gst: [{ key: 'state', label: 'State', options: INDIAN_STATES, select: true }],
  // a bid encloses the short version unless its RFP asks for the full accounts
  financial_statement: [
    { key: 'financial_year', label: 'Financial year', placeholder: 'e.g. 2024-25' },
    { key: 'version', label: 'Version', options: ['Short', 'Full'], select: true },
  ],
  cv: [
    { key: 'name', label: 'Name' }, { key: 'role', label: 'Proposed role' },
    { key: 'total_years', label: 'Total experience (years)', kind: 'number' }, { key: 'qualification', label: 'Highest qualifications (up to two)', placeholder: 'e.g. MBA, B.Tech' },
    { key: 'skills', label: 'Skills (comma-separated)', kind: 'list' }, { key: 'certifications', label: 'Certifications (comma-separated)', kind: 'list' },
  ],
};
const CAT_META: Record<string, { icon: string; desc: string }> = {
  experience: { icon: 'briefcase', desc: 'Projects: each starts from its purchase order / work order; open one to add its details, completion certificate, evidence and RFP' },
  statutory: { icon: 'shield-check', desc: 'Incorporation, PAN, GST, certifications, financials, power of attorney' },
  hr: { icon: 'users', desc: 'CVs of your people; open one to add the UTL code and upload certificates' },
  literature: { icon: 'book-open', desc: 'Reusable write-ups: profile, methodology, security, O&M' },
  uploads: { icon: 'upload', desc: 'Documents added from your device' },
};

/** Documents Library: the prototype's tabs, table, insights panel and preview, backed by the API. */
@Component({
  selector: 'bc-library',
  imports: [FormsModule, Icons, NgTemplateOutlet],
  template: `
    <div class="wrap">
      <div class="row between wrapx" style="margin-bottom:14px">
        <div><h1 class="v-title">Documents Library</h1><p class="sub">Everything the agent has learned about your organisation. Click a row to preview the document.</p></div>
        <div class="row wrapx">
          <input class="inp" placeholder="Search the library" style="width:220px" [ngModel]="q()" (ngModelChange)="q.set($event)" aria-label="Search">
          <select class="inp" style="width:140px" [(ngModel)]="uploadSensitivity" aria-label="Sensitivity for new uploads">
            <option value="public">Public</option><option value="internal">Internal</option><option value="confidential">Confidential</option><option value="restricted">Restricted</option></select>
          <button class="btn primary" (click)="picker.click()"><lucide-icon name="upload"></lucide-icon>{{ tab() === 'experience' ? 'Add project (upload PO)' : 'Add documents' }}</button>
          <input #picker type="file" multiple hidden (change)="onPick(picker)">
        </div>
      </div>
      <div class="tabs">
        @for (c of categories; track c.key) {
          <button [class.on]="tab() === c.key" (click)="tab.set(c.key)"><lucide-icon [name]="meta[c.key].icon"></lucide-icon>{{ c.label }}<span class="count">{{ countOf(c.key) }}</span></button>
        }
      </div>
      @if (message()) { <div class="notice"><lucide-icon name="info"></lucide-icon><div>{{ message() }}</div></div> }
      <div class="lib-grid" [style.grid-template-columns]="fullWidth() ? '1fr' : null">
        <div class="card" style="padding:6px 8px" [style.outline]="dragging() ? '2px dashed var(--blue)' : null"
             (dragover)="$event.preventDefault(); dragging.set(true)" (dragleave)="dragging.set(false)" (drop)="onDrop($event)">
          @if (visible().length) {
            <div class="tbl-wrap"><table class="tbl">
              @switch (tab()) {
                @case ('experience') { <tr>
                  @for (c of projectColumns; track c[0]) {
                    <th class="sortable" [attr.aria-sort]="sort().key === c[0] ? (sort().dir === 1 ? 'ascending' : 'descending') : 'none'" (click)="sortBy(c[0])">{{ c[1] }}@if (sort().key === c[0]) { <lucide-icon [name]="sort().dir === 1 ? 'arrow-up' : 'arrow-down'"></lucide-icon> }</th>
                  }<th>Tags</th><th></th></tr>
                  @for (d of visible(); track d.id) { <tr class="click" (click)="open(d)">
                    <td class="t1">{{ m(d, 'short_name') || d.title }}
                      <div class="muted small">PO no. {{ m(d, 'po_number') || '—' }}</div>
                      @if (samePo(d).length) { <div><span class="chip bad" [title]="'Same PO number as: ' + samePoNames(d)">Duplicate PO no.</span></div> }
                      @if (d.status !== 'ready') { <div><ng-container *ngTemplateOutlet="status; context: { $implicit: d }"></ng-container></div> }
                      @else if (extracting(d)) { <div><span class="chip blue"><span class="spin"></span>Reading details</span></div> }
                      @else if (autoKeys(d).length) { <div><span class="chip warn" title="Check the details filled automatically">Check details</span></div> }</td>
                    <td style="white-space:nowrap">{{ m(d, 'po_date') ? shortDate(m(d, 'po_date')) : '—' }}</td>
                    <td>{{ m(d, 'client') || '—' }}@if (m(d, 'ministry')) { <div class="muted small">{{ m(d, 'ministry') }}</div> }</td>
                    <td style="white-space:nowrap">{{ d.metadata['value_cr'] ? '₹ ' + d.metadata['value_cr'] + ' Cr' : '—' }}</td>
                    <td><div class="tags">@for (t of tags(d); track t) { <span class="tag">{{ t }}</span> }</div></td>
                    <ng-container *ngTemplateOutlet="actions; context: { $implicit: d }"></ng-container></tr> } }
                @case ('statutory') { <tr><th>Document type</th><th>Document number</th><th>Validity</th><th>Updated</th><th>Size</th><th></th></tr>
                  @for (d of visible(); track d.id) { <tr class="click" (click)="preview(d)"><td class="t1">{{ typeLabel(d) }}@if (d.docType === 'certification') { <div class="muted small">Certification</div> }
                      @else if (d.docType === 'financial_statement') { <div class="muted small" title="A bid encloses the short version unless its RFP asks for the full accounts">{{ m(d, 'version') ? m(d, 'version') + ' version' : 'Version not set: short or full?' }}</div> }</td>
                    <td>@switch (d.status) { @case ('ready') { {{ m(d, 'number') || m(d, 'cin') || m(d, 'pan') || m(d, 'gstin') || '—' }} } @case ('failed') { <span class="chip bad" [title]="d.error || ''">Could not read</span> } @default { <span class="chip blue" [title]="readingHint"><span class="spin"></span>{{ readingLabel(d) }}</span> } }</td>
                    <td>@if (valid(d); as v) { <span [class]="v.cls">{{ v.text }}</span> } @else if (permanent(d)) { <span class="chip">Permanent</span> } @else { <span class="chip">—</span> }</td>
                    <td style="white-space:nowrap">{{ shortDate(d.updatedAt) }}</td><td style="white-space:nowrap">{{ size(d.fileSize) }}</td>
                    <ng-container *ngTemplateOutlet="actions; context: { $implicit: d }"></ng-container></tr> } }
                @case ('hr') { <tr><th>Name</th><th>Proposed role</th><th>Experience</th><th>Highest qualifications</th><th>Skills</th><th>Certifications</th><th></th></tr>
                  @for (d of visible(); track d.id) { <tr class="click" (click)="d.docType === 'cv' ? open(d) : preview(d)">
                    <td class="t1">{{ m(d, 'name') || d.title }}@if (d.docType !== 'cv') { <div class="muted small">{{ labels[d.docType] ?? d.docType }}</div> }
                      @if (m(d, 'employee_code')) { <div class="muted small">UTL code {{ m(d, 'employee_code') }}</div> }
                      @if (samePerson(d).length) { <div><span class="chip bad" [title]="'Probably the same person as: ' + samePersonNames(d)">Possible duplicate</span></div> }
                      @if (d.status !== 'ready') { <div><ng-container *ngTemplateOutlet="status; context: { $implicit: d }"></ng-container></div> }
                      @else if (extracting(d)) { <div><span class="chip blue"><span class="spin"></span>Reading details</span></div> }
                      @else if (autoKeys(d).length) { <div><span class="chip warn" title="Check the details filled automatically">Check details</span></div> }</td>
                    <td>{{ m(d, 'role') || '—' }}</td>
                    <td style="white-space:nowrap">{{ d.metadata['total_years'] ? d.metadata['total_years'] + ' years' : '—' }}</td>
                    <td>{{ m(d, 'qualification') || '—' }}</td>
                    <td><div class="tags">@for (t of list(d, 'skills').slice(0, 6); track t) { <span class="tag">{{ t }}</span> }@if (list(d, 'skills').length > 6) { <span class="tag">+{{ list(d, 'skills').length - 6 }}</span> }</div></td>
                    <td><div class="tags">@for (t of list(d, 'certifications'); track t) { <span class="tag">{{ t }}</span> }</div></td>
                    <ng-container *ngTemplateOutlet="actions; context: { $implicit: d }"></ng-container></tr> } }
                @default { <tr><th>Title</th><th>Type</th><th>Pages</th><th>Added</th><th>Status</th><th></th></tr>
                  @for (d of visible(); track d.id) { <tr class="click" (click)="preview(d)"><td class="t1">{{ d.title }}<div class="muted small">{{ d.originalName }}</div></td><td>{{ d.docType.replaceAll('_', ' ') }}</td><td>{{ d.pageCount ?? '—' }}</td><td>{{ d.createdAt.slice(0, 10) }}</td><td><ng-container *ngTemplateOutlet="status; context: { $implicit: d }"></ng-container></td><ng-container *ngTemplateOutlet="actions; context: { $implicit: d }"></ng-container></tr> } }
              }
            </table></div>
          } @else {
            <div class="drop" (click)="picker.click()"><lucide-icon name="upload"></lucide-icon><b>{{ q() ? 'No matches' : 'No documents yet' }}</b>{{ q() ? 'Try another search term.' : 'Add documents to teach the agent.' }}</div>
          }
        </div>
        @if (!fullWidth()) {
          <div class="card insight">
            @switch (tab()) {
              @case ('literature') {
                <h3>How literature is used</h3>
                <p class="small muted">The agent adapts these write-ups to each RFP’s scope and evaluation criteria, then cites the clauses it responded to. Your originals stay unchanged.</p>
              }
              @default {
                <h3>Uploaded documents</h3>
                <p class="small muted">Other documents kept for reuse. Documents for one bid only (EMD, power of attorney, signed forms) are kept with that bid, on its Bid response page.</p>
              }
            }
          </div>
        }
      </div>
      <p class="small muted" style="margin-top:14px">{{ meta[tab()].desc }}. Drop files anywhere on the table to add them to this category.</p>
    </div>

    <ng-template #status let-d>
      @switch (d.status) {
        @case ('ready') { <span class="chip ok"><lucide-icon name="check"></lucide-icon>Trained</span> @if (autoKeys(d).length) { <span class="chip warn" title="Check the details filled automatically">Check details</span> } }
        @case ('failed') { <span class="chip bad" [title]="d.error || ''">Failed</span> }
        @default { <span class="chip blue" [title]="readingHint"><span class="spin"></span>{{ readingLabel(d) }}</span> }
      }
    </ng-template>

    <ng-template #actions let-d>
      <td style="text-align:right;white-space:nowrap" (click)="$event.stopPropagation()">
        <button class="ib" [disabled]="d.status !== 'ready'" (click)="download(d)" title="Download PDF" aria-label="Download PDF"><lucide-icon name="download"></lucide-icon></button>
        <button class="ib" (click)="open(d)" title="Edit details" aria-label="Edit details"><lucide-icon name="pencil"></lucide-icon></button>
        <button class="ib" style="color:var(--bad)" (click)="remove(d)" title="Delete" aria-label="Delete"><lucide-icon name="trash-2"></lucide-icon></button>
      </td>
    </ng-template>

    @if (previewing(); as d) {
      <div class="modal-bg open" (click)="closePreview()">
        <div class="modal" style="max-width:1200px;height:calc(100% - 20px)" (click)="$event.stopPropagation()">
          <div class="modal-h"><div><h3>{{ d.title }}</h3><div class="muted small">{{ d.pageCount ? d.pageCount + ' page' + (d.pageCount > 1 ? 's' : '') + ', ' : '' }}{{ categoryLabel(d.category) }}</div></div>
            <div class="row"><button class="btn sm" (click)="edit(d)">Edit details</button><button class="ib" (click)="closePreview()" aria-label="Close"><lucide-icon name="x"></lucide-icon></button></div></div>
          <div class="modal-b" style="padding:0;background:var(--canvas);display:flex;flex-direction:column">
            @if (previewUrl(); as url) { <iframe [src]="url" style="flex:1;width:100%;min-height:0;border:0" title="Document preview"></iframe> }
            @else { <p class="muted" style="padding:24px">{{ d.status === 'ready' ? 'Loading…' : 'The preview is available once the document has been read.' }}</p> }
          </div>
        </div>
      </div>
    }

    @if (editing(); as d) {
      <div class="backdrop" (click)="editing.set(null)"></div>
      <aside class="drawer" aria-label="Document details">
        <div class="dh"><b>Document details</b><button class="ib" (click)="editing.set(null)" aria-label="Close"><lucide-icon name="x"></lucide-icon></button></div>
        <div class="db">
          <div class="field"><label>Title</label><input class="inp" [(ngModel)]="form.title"></div>
          <div class="row">
            <div class="field grow"><label>Category</label><select class="inp" [(ngModel)]="form.category">@for (c of categories; track c.key) { <option [value]="c.key">{{ c.label }}</option> }</select></div>
            <div class="field grow"><label>Type</label><select class="inp" [(ngModel)]="form.docType">@for (t of typesFor(form.category); track t) { <option [value]="t">{{ labels[t] ?? t.replaceAll('_', ' ') }}</option> }</select></div>
          </div>
          @for (f of fieldsFor(form.category, form.docType); track f.key) {
            <div class="field"><label>{{ f.label }}</label>
              @if (f.select) {
                <select class="inp" [(ngModel)]="form.fields[f.key]"><option value="">—</option>@for (o of f.options; track o) { <option [value]="o">{{ o }}</option> }</select>
              } @else {
                <input class="inp" [type]="f.kind === 'number' ? 'number' : 'text'" [attr.list]="f.options ? 'opts-' + f.key : null" [(ngModel)]="form.fields[f.key]" [placeholder]="f.placeholder ?? ''">
                @if (f.options) { <datalist [id]="'opts-' + f.key">@for (o of f.options; track o) { <option [value]="o"></option> }</datalist> }
              }
            </div>
          }
          <div class="row">
            <div class="field grow"><label>Valid until</label><input class="inp" type="date" [(ngModel)]="form.validUntil"></div>
            <div class="field grow"><label>Sensitivity</label><select class="inp" [(ngModel)]="form.sensitivity">
              <option value="public">Public</option><option value="internal">Internal</option><option value="confidential">Confidential</option><option value="restricted">Restricted (never sent)</option></select></div>
          </div>
          <div class="field"><label>Details used for matching (JSON)</label><textarea class="inp" rows="9" [(ngModel)]="form.details"></textarea>
            @if (hint()) { <span class="small muted">Example: {{ hint() }}</span> }
            @if (autoKeys(d).length) { <span class="small" style="color:var(--warn)">Filled automatically, please check: {{ autoKeys(d).join(', ') }}</span> }</div>
          @if (formError()) { <div class="notice warn"><lucide-icon name="info"></lucide-icon><div>{{ formError() }}</div></div> }
          <div class="row wrapx"><button class="btn sm" (click)="reprocess(d)"><lucide-icon name="rotate-ccw"></lucide-icon>Read again</button><button class="btn sm quiet" style="color:var(--bad)" (click)="remove(d)"><lucide-icon name="trash-2"></lucide-icon>Delete</button></div>
        </div>
        <div class="df"><button class="btn" (click)="editing.set(null)">Cancel</button><button class="btn primary" (click)="save(d)">Save details</button></div>
      </aside>
    }`,
})
export class LibraryPage implements OnInit {
  private api = inject(Api);
  private sanitizer = inject(DomSanitizer);
  private router = inject(Router);
  readonly tabParam = input<string | undefined>(undefined, { alias: 'tab' });
  categories = CATEGORIES;
  meta = CAT_META;
  labels = DOC_TYPE_LABELS;
  readingLabel = readingLabel;
  readingHint = READING_HINT;
  docs = signal<LibraryDoc[]>([]);
  tab = signal<Category>('experience');
  q = signal('');
  dragging = signal(false);
  message = signal('');
  editing = signal<LibraryDoc | null>(null);
  previewing = signal<LibraryDoc | null>(null);
  previewUrl = signal<SafeResourceUrl | null>(null);
  private previewObjectUrl: string | null = null;
  /** The edit panel as it was opened: Save writes only what differs from it. */
  private opened: { details: Record<string, unknown>; fields: Record<string, string>; validUntil: string } | null = null;
  formError = signal('');
  uploadSensitivity: Sensitivity = 'internal';
  form: EditForm = { title: '', category: 'experience', docType: 'other', fields: {}, sensitivity: 'internal', validUntil: '', details: '{}' };

  /** Experience shows projects; their certificates, evidence and RFP open on the project page. */
  /** Experience shows projects and HR candidates; their related documents open on the project or candidate page. */
  listed = computed(() => this.docs().filter(d => !parentOf(d)));
  visible = computed(() => {
    const q = this.q().trim().toLowerCase();
    const rows = this.listed().filter(d => d.category === this.tab() && (!q || JSON.stringify(d).toLowerCase().includes(q)));
    const { key, dir } = this.sort();
    if (this.tab() !== 'experience' || !key) return rows;
    // empty values go last in either direction
    return [...rows].sort((a, b) => {
      const x = this.sortValue(a, key), y = this.sortValue(b, key);
      if (x === null || y === null) return x === y ? 0 : x === null ? 1 : -1;
      return (x < y ? -1 : x > y ? 1 : 0) * dir;
    });
  });
  /** Sortable Experience columns: [key, header]. */
  projectColumns: [ProjectSort, string][] = [['project', 'Project'], ['po_date', 'PO date'], ['client', 'Client'], ['value', 'Value']];
  sort = signal<{ key: ProjectSort | null; dir: 1 | -1 }>({ key: null, dir: 1 });
  children = computed(() => {
    const by: Record<string, LibraryDoc[]> = {};
    this.docs().forEach(d => { const p = parentOf(d); if (p) (by[p] ??= []).push(d); });
    return by;
  });
  fullWidth = computed(() => ['statutory', 'experience', 'hr'].includes(this.tab()));
  hint = () => DETAIL_HINTS[this.form.docType] ?? '';
  valid = (d: LibraryDoc) => validity(d.validUntil);
  m = (d: LibraryDoc, k: string) => { const v = d.metadata[k]; return v === undefined || v === null ? '' : String(v); };
  list = (d: LibraryDoc, k: string) => { const v = d.metadata[k]; return Array.isArray(v) ? v.map(String) : []; };
  tags = (d: LibraryDoc) => this.list(d, 'domains');
  categoryLabel = (c: string) => CATEGORIES.find(x => x.key === c)?.label ?? c;
  /** "CMMI Level 5", "ISO 9001:2015", "GST Registration – Delhi"; the generic type otherwise. */
  typeLabel = (d: LibraryDoc) => {
    if (d.docType === 'certification' && this.m(d, 'standard'))
      return `${this.m(d, 'standard')}${d.metadata['level'] ? ' Level ' + d.metadata['level'] : ''}`;
    if (d.docType === 'gst' && this.m(d, 'state')) return `GST Registration – ${this.m(d, 'state')}`;
    if (d.docType === 'financial_statement' && this.m(d, 'financial_year')) return `Audited Financial Statements – FY ${this.m(d, 'financial_year')}`;
    return DOC_TYPE_LABELS[d.docType] ?? d.title;
  };
  permanent = (d: LibraryDoc) => ['incorporation', 'moa_aoa', 'pan', 'gst'].includes(d.docType);
  shortDate = (iso: string) => new Date(iso).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
  size = (bytes: number | null) => bytes === null ? '—'
    : bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  related = (d: LibraryDoc) => this.children()[d.id] ?? [];
  /** Other projects with the same PO number: most likely the same order uploaded twice (e.g. two scans). */
  samePo = (d: LibraryDoc) => {
    const key = poKey(d.metadata['po_number']);
    return key ? this.listed().filter(x => x.id !== d.id && x.category === 'experience' && poKey(x.metadata['po_number']) === key) : [];
  };
  samePoNames = (d: LibraryDoc) => this.samePo(d).map(x => this.m(x, 'short_name') || x.title).join(', ');
  extracting = (d: LibraryDoc) => ['queued', 'running'].includes(String((d.metadata['extraction'] as { status?: string } | undefined)?.status ?? ''));
  lastUpdated = (d: LibraryDoc) => [d, ...this.related(d)].map(x => x.updatedAt).sort().at(-1)!;

  constructor() {
    const destroy = inject(DestroyRef);
    let tick = 0;
    timer(0, 4000).pipe(
      switchMap(() => {
        const busy = this.docs().some(d => d.status === 'uploaded' || d.status === 'processing' || this.extracting(d));
        if (!busy && tick++ % 8 !== 0) return EMPTY;
        return this.api.library().pipe(catchError(() => EMPTY));
      }),
      takeUntilDestroyed(destroy),
    ).subscribe(list => this.docs.set(list));
  }

  ngOnInit(): void { const t = this.tabParam(); if (t && CATEGORIES.some(c => c.key === t)) this.tab.set(t as Category); }
  countOf(cat: Category): number { return this.listed().filter(d => d.category === cat).length; }
  /** First click sorts ascending, the next descending. */
  sortBy(key: ProjectSort): void {
    this.sort.update(s => ({ key, dir: s.key === key ? (-s.dir as 1 | -1) : 1 }));
  }
  private sortValue(d: LibraryDoc, key: ProjectSort): string | number | null {
    switch (key) {
      case 'project': return (this.m(d, 'short_name') || d.title).toLowerCase();
      case 'po_date': return this.m(d, 'po_date') || null;                       // ISO dates sort as text
      case 'client': return this.m(d, 'client').toLowerCase() || null;
      case 'value': { const v = Number(d.metadata['value_cr']); return d.metadata['value_cr'] == null || !isFinite(v) ? null : v; }
    }
  }
  open(d: LibraryDoc): void {
    if (d.category === 'experience') this.router.navigate(['/library/projects', d.id]);
    else if (d.category === 'hr' && d.docType === 'cv') this.router.navigate(['/library/people', d.id]);
    else this.edit(d);
  }
  /** Other candidates who are probably the same person (same UTL code or similar name). */
  samePerson = (d: LibraryDoc) => d.docType !== 'cv' ? []
    : this.listed().filter(x => x.id !== d.id && x.category === 'hr' && x.docType === 'cv' && samePersonReason(d, x));
  samePersonNames = (d: LibraryDoc) => this.samePerson(d).map(x => `${this.m(x, 'name') || x.title} (${samePersonReason(d, x)})`).join(', ');
  typesFor(cat: Category): string[] { return CATEGORIES.find(c => c.key === cat)?.types ?? ['other']; }
  autoKeys(d: LibraryDoc): string[] { return (d.metadata['_auto'] as string[] | undefined) ?? []; }

  onPick(input: HTMLInputElement): void { if (input.files?.length) this.upload([...input.files]); input.value = ''; }
  onDrop(e: DragEvent): void { e.preventDefault(); this.dragging.set(false); const files = [...(e.dataTransfer?.files ?? [])]; if (files.length) this.upload(files); }
  upload(files: File[]): void {
    this.message.set(`Uploading ${files.length} file(s)…`);
    const docType = this.tab() === 'experience' ? 'work_order' : undefined;
    this.api.upload(files, this.tab(), this.uploadSensitivity, { docType }).subscribe({
      next: r => {
        const created = r.created;
        this.docs.update(l => [...created, ...l]);
        const added = !created.length ? '' : docType
          ? `${created.length} project(s) added; their details are read from the purchase order. `
          : `${created.length} file(s) added. They are being read and indexed on this server. `;
        this.message.set((added + duplicateNote(r)).trim());
      },
      error: e => this.message.set(errorText(e)),
    });
  }

  preview(d: LibraryDoc): void {
    this.closePreview();
    this.previewing.set(d);
    if (d.status !== 'ready') return;
    this.api.docFile(d.id, true).subscribe({
      next: blob => {
        if (this.previewing()?.id !== d.id) return;          // another row was opened meanwhile
        this.previewObjectUrl = URL.createObjectURL(blob);
        this.previewUrl.set(this.sanitizer.bypassSecurityTrustResourceUrl(this.previewObjectUrl));
      },
      error: e => { if (this.previewing()?.id === d.id) { this.previewing.set(null); this.message.set(errorText(e)); } },
    });
  }

  closePreview(): void {
    this.previewing.set(null);
    this.previewUrl.set(null);
    if (this.previewObjectUrl) { URL.revokeObjectURL(this.previewObjectUrl); this.previewObjectUrl = null; }
  }

  download(d: LibraryDoc): void {
    this.api.docFile(d.id, true).subscribe({
      next: blob => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a'); a.href = url; a.download = `${d.title}.pdf`; a.click();
        setTimeout(() => URL.revokeObjectURL(url), 60_000);
      },
      error: e => this.message.set(errorText(e)),
    });
  }

  fieldsFor(category: Category, docType: string): FieldDef[] {
    return [...(category === 'statutory' ? STATUTORY_FIELDS : []), ...(TYPE_FIELDS[docType] ?? [])];
  }

  edit(d: LibraryDoc): void {
    const { _auto, ...details } = d.metadata as Record<string, unknown>; void _auto;
    const fields: Record<string, string> = {};
    for (const f of this.fieldsFor(d.category, d.docType)) {
      const v = details[f.key];
      fields[f.key] = v == null ? '' : Array.isArray(v) ? v.join(', ') : String(v);
      delete details[f.key];
    }
    this.form = { title: d.title, category: d.category, docType: d.docType, fields, sensitivity: d.sensitivity, validUntil: d.validUntil ?? '', details: JSON.stringify(details, null, 2) };
    this.opened = { details: JSON.parse(JSON.stringify(details)), fields: { ...fields }, validUntil: d.validUntil ?? '' };
    this.formError.set(''); this.closePreview(); this.editing.set(d);
  }

  /** Details plus the plain fields as metadata. A field of a newly chosen type that was never filled in is left
   *  out, so a value for it that is still in the JSON box is kept. */
  private asMetadata(details: Record<string, unknown>, fields: Record<string, string>, defs: FieldDef[]): Record<string, unknown> | string {
    const out = { ...details };
    for (const f of defs) {
      if (fields[f.key] === undefined) continue;
      const raw = String(fields[f.key] ?? '').trim();
      delete out[f.key];
      if (!raw) continue;
      if (f.kind === 'number') {
        if (!isFinite(Number(raw))) return `${f.label} must be a number`;
        out[f.key] = Number(raw);
      } else out[f.key] = f.kind === 'list' ? raw.split(',').map(x => x.trim()).filter(Boolean) : raw;
    }
    return out;
  }

  save(d: LibraryDoc): void {
    let metadata: Record<string, unknown>;
    try { metadata = JSON.parse(this.form.details || '{}'); if (typeof metadata !== 'object' || Array.isArray(metadata) || metadata === null) throw new Error(); }
    catch { this.formError.set('Details must be a valid JSON object, for example {"value_cr": 12.6}'); return; }
    // fields of the type the document had and of the type chosen now, so a changed type keeps what was entered
    const defs = [...new Map([...this.fieldsFor(d.category, d.docType), ...this.fieldsFor(this.form.category, this.form.docType)]
      .map(f => [f.key, f])).values()];
    const after = this.asMetadata(metadata, this.form.fields, defs);
    if (typeof after === 'string') { this.formError.set(after); return; }
    const before = this.asMetadata(this.opened?.details ?? {}, this.opened?.fields ?? {}, defs) as Record<string, unknown>;
    // Only what was changed in the panel is written, onto the document as it is now: details read from the
    // document while the panel was open are kept.
    const current = this.docs().find(x => x.id === d.id) ?? d;
    const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
    const changed = [...new Set([...Object.keys(before), ...Object.keys(after)])].filter(k => !same(before[k], after[k]));
    const { _auto, ...merged } = current.metadata as Record<string, unknown>;
    // automatic values the panel did not show (they arrived after it opened) stay marked for checking
    const unseen = ((_auto as string[] | undefined) ?? []).filter(k => !changed.includes(k) && !same(merged[k], before[k]));
    for (const k of changed) { if (after[k] === undefined) delete merged[k]; else merged[k] = after[k]; }
    if (unseen.length) merged['_auto'] = unseen;
    const validUntil = this.form.validUntil !== (this.opened?.validUntil ?? '') ? (this.form.validUntil || null) : current.validUntil;
    this.api.updateDoc(d.id, { title: this.form.title, category: this.form.category, docType: this.form.docType, sensitivity: this.form.sensitivity, validUntil, metadata: merged })
      .subscribe({ next: u => { this.docs.update(l => l.map(x => x.id === u.id ? u : x)); this.editing.set(null); }, error: e => this.formError.set(errorText(e)) });
  }

  reprocess(d: LibraryDoc): void { this.api.reprocess(d.id).subscribe(() => { this.docs.update(l => l.map(x => x.id === d.id ? { ...x, status: 'uploaded' } : x)); this.editing.set(null); }); }
  remove(d: LibraryDoc): void {
    const n = this.related(d).length;
    if (!confirm(`Delete “${this.m(d, 'short_name') || d.title}” from the library${n ? ` with its ${n} related document(s)` : ''}? It will also be removed from any bid drafts.`)) return;
    this.api.deleteDoc(d.id).subscribe(() => { this.docs.update(l => l.filter(x => x.id !== d.id && parentOf(x) !== d.id)); this.editing.set(null); });
  }
}
