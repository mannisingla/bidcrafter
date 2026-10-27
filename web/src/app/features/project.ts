import { NgTemplateOutlet } from '@angular/common';
import { Component, DestroyRef, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { EMPTY, catchError, switchMap, timer } from 'rxjs';
import { Api, errorText, openBlob } from '../core/api';
import { Icons } from '../core/icons';
import { DOC_TYPE_LABELS, LibraryDoc, PROJECT_TAGS, READING_HINT, duplicateNote, poKey, readingLabel } from '../core/models';

interface ProjectForm {
  short_name: string; client: string; project: string; ministry: string; scope: string; value_cr: string;
  po_number: string; po_date: string; duration: string; completion_date: string; tags: string[];
}
const TEXT_KEYS = ['short_name', 'client', 'project', 'ministry', 'scope', 'po_number', 'po_date', 'duration', 'completion_date'] as const;
const FIELD_LABEL: Record<string, string> = {
  short_name: 'short name', client: 'client', project: 'project title', ministry: 'state / ministry', scope: 'description',
  value_cr: 'value', po_number: 'PO number', po_date: 'PO date', duration: 'duration', completion_date: 'completion date', domains: 'tags',
};
const RELATED_TYPES = ['completion_certificate', 'performance_certificate', 'evidence', 'other'];

/** One Experience project: its details, related documents and the RFP used to fill them. */
@Component({
  selector: 'bc-project',
  imports: [FormsModule, Icons, NgTemplateOutlet, RouterLink],
  template: `
    <div class="wrap" style="max-width:1100px">
      <a class="small" routerLink="/library" [queryParams]="{ tab: 'experience' }"><lucide-icon name="chevron-left"></lucide-icon>Experience</a>
      @if (project(); as p) {
        <div class="row between wrapx" style="margin:8px 0 14px">
          <div><h1 class="v-title">{{ form.short_name || form.project || p.title }}</h1>
            <p class="sub">Purchase order: {{ p.originalName }} · Updated {{ shortDate(lastUpdated()) }} · {{ size(p.fileSize) }}</p></div>
          <div class="row wrapx">
            <button class="btn sm" [disabled]="p.status !== 'ready'" (click)="view(p)"><lucide-icon name="eye"></lucide-icon>View PO</button>
            <button class="btn sm" [disabled]="p.status !== 'ready'" (click)="download(p)"><lucide-icon name="download"></lucide-icon>Download</button>
            <button class="btn sm" [disabled]="p.status !== 'ready' || extracting(p)" (click)="extract(p)" title="Fill empty and automatically filled fields from the purchase order"><lucide-icon name="sparkles"></lucide-icon>Read details from PO</button>
            <button class="btn sm quiet" style="color:var(--bad)" (click)="removeProject(p)"><lucide-icon name="trash-2"></lucide-icon>Delete project</button>
            <button class="btn primary" (click)="save(p)" [disabled]="saving()">{{ saving() ? 'Saving…' : 'Save' }}</button>
          </div>
        </div>
        @if (message()) { <div class="notice" [class.warn]="messageWarn()"><lucide-icon name="info"></lucide-icon><div>{{ message() }}</div></div> }
        @if (p.status === 'failed') { <div class="notice warn"><lucide-icon name="info"></lucide-icon><div>The purchase order could not be read: {{ p.error || 'unknown error' }}
          <button class="btn sm" style="margin-left:8px" (click)="reread(p)">Read again</button></div></div> }
        @else if (p.status !== 'ready') { <div class="notice" [title]="readingHint"><span class="spin"></span><div>{{ readingLabel(p) }}. The value and PO date are filled in when the purchase order has been read; scanned pages take about 10 seconds each.</div></div> }
        @switch (extraction(p).status) {
          @case ('queued') { <div class="notice"><span class="spin"></span><div>Reading the project details from the purchase order…</div></div> }
          @case ('running') { <div class="notice"><span class="spin"></span><div>Reading the project details from the purchase order…</div></div> }
          @case ('failed') { <div class="notice warn"><lucide-icon name="info"></lucide-icon><div>The details could not be read from the purchase order: {{ extraction(p).error }}</div></div> }
        }
        @if (samePo().length) {
          <div class="notice warn"><lucide-icon name="flag"></lucide-icon><div>Possible duplicate: PO number {{ form.po_number }} is also on
            @for (x of samePo(); track x.id; let last = $last) { <a [routerLink]="['/library/projects', x.id]">{{ x.metadata['short_name'] || x.title }}</a>{{ last ? '' : ', ' }} }.
            If it is the same order, keep one project and delete the other.</div></div>
        }
        @if (autoKeys().length) { <div class="notice warn"><lucide-icon name="info"></lucide-icon><div>Filled automatically, please check and save: {{ autoKeys().join(', ') }}.</div></div> }

        <div class="card" (input)="dirty = true" (change)="dirty = true">
          <h3>Project details</h3>
          <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:0 16px;margin-top:12px">
            <div class="field"><label>Short name</label><input class="inp" [(ngModel)]="form.short_name" placeholder="e.g. Odisha IFMS"></div>
            <div class="field"><label>Client name</label><input class="inp" [(ngModel)]="form.client" placeholder="Department, agency or PSU"></div>
            <div class="field"><label>State / Ministry</label><input class="inp" [(ngModel)]="form.ministry" placeholder="e.g. Government of Odisha"></div>
          </div>
          <div class="field"><label>Project title</label><input class="inp" [(ngModel)]="form.project"></div>
          <div class="field"><label>Project description</label><textarea class="inp" rows="5" [(ngModel)]="form.scope" placeholder="Scope of work delivered"></textarea></div>
          <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:0 16px">
            <div class="field"><label>PO number</label><input class="inp" [(ngModel)]="form.po_number" placeholder="Work order / PO no."></div>
            <div class="field"><label>Project value (₹ crore)</label><input class="inp" type="number" step="any" min="0" [(ngModel)]="form.value_cr"></div>
            <div class="field"><label>PO date</label><input class="inp" type="date" [(ngModel)]="form.po_date"></div>
            <div class="field"><label>Duration</label><input class="inp" [(ngModel)]="form.duration" placeholder="e.g. 36 months"></div>
            <div class="field"><label>Completion date</label><input class="inp" type="date" [(ngModel)]="form.completion_date"></div>
          </div>
          <div class="field"><label>Tags</label>
            <div class="tags" style="align-items:center;gap:6px">
              @for (t of form.tags; track t) { <span class="chip">{{ t }}<button class="ib" style="width:18px;height:18px" (click)="removeTag(t)" [attr.aria-label]="'Remove ' + t"><lucide-icon name="x"></lucide-icon></button></span> }
              <input class="inp" style="width:220px" list="project-tags" [(ngModel)]="tagInput" (keydown.enter)="addTag()" (change)="addTag()" placeholder="Add a tag (Manpower, AMC…)">
              <datalist id="project-tags">@for (t of tagSuggestions(); track t) { <option [value]="t"></option> }</datalist>
            </div>
          </div>
        </div>

        <div class="section-h"><h2>Related documents</h2>
          <div class="row wrapx">
            <select class="inp" style="width:210px" [(ngModel)]="relType" aria-label="Type of document to add">@for (t of relatedTypes; track t) { <option [value]="t">{{ labels[t] }}</option> }</select>
            <button class="btn sm primary" (click)="relPicker.click()"><lucide-icon name="upload"></lucide-icon>Upload</button>
            <input #relPicker type="file" multiple hidden (change)="uploadRelated(relPicker, relType)">
            @if (attachable().length) {
              <select class="inp" style="width:230px" [ngModel]="''" (ngModelChange)="attach($event)" aria-label="Attach a document already in the library">
                <option value="">Attach from library…</option>@for (d of attachable(); track d.id) { <option [value]="d.id">{{ d.title }}</option> }</select>
            }
          </div>
        </div>
        <div class="card" style="padding:6px 8px">
          @if (related().length) {
            <div class="tbl-wrap"><table class="tbl"><tr><th>Type</th><th>Document</th><th>Updated</th><th>Size</th><th></th></tr>
              @for (d of related(); track d.id) { <ng-container *ngTemplateOutlet="row; context: { $implicit: d }"></ng-container> }
            </table></div>
          } @else { <p class="muted small" style="padding:10px 12px">Add the completion certificate, performance certificate and documentary evidence of features or functionality delivered.</p> }
        </div>

        <div class="section-h"><h2>RFP / tender document</h2>
          <div class="row"><button class="btn sm" (click)="rfpPicker.click()"><lucide-icon name="upload"></lucide-icon>Upload RFP</button>
            <input #rfpPicker type="file" hidden (change)="uploadRelated(rfpPicker, 'project_rfp')"></div>
        </div>
        <div class="card" style="padding:6px 8px">
          @if (rfps().length) {
            <div class="tbl-wrap"><table class="tbl"><tr><th>Type</th><th>Document</th><th>Updated</th><th>Size</th><th></th></tr>
              @for (d of rfps(); track d.id) { <ng-container *ngTemplateOutlet="row; context: { $implicit: d }"></ng-container> }
            </table></div>
          } @else { <p class="muted small" style="padding:10px 12px">Upload the RFP of this project to fill the project title, client, state / ministry, description, duration and tags. Only the opening pages and the scope and background sections are sent, redacted, to the model; fields you have already filled are kept.</p> }
        </div>
      } @else if (notFound()) {
        <div class="notice warn" style="margin-top:14px"><lucide-icon name="info"></lucide-icon><div>This project no longer exists.</div></div>
      } @else { <p class="muted" style="margin-top:14px">Loading…</p> }
    </div>

    <ng-template #row let-d>
      <tr>
        <td class="t1" style="white-space:nowrap">{{ labels[d.docType] ?? d.docType }}</td>
        <td>{{ d.title }}@if (d.status === 'failed') { <div><span class="chip bad" [title]="d.error || ''">Could not read</span></div> } @else if (d.status !== 'ready') { <div><span class="chip blue" [title]="readingHint"><span class="spin"></span>{{ readingLabel(d) }}</span></div> }
          @switch (extraction(d).status) {
            @case ('queued') { <div><span class="chip blue"><span class="spin"></span>Reading project details</span></div> }
            @case ('running') { <div><span class="chip blue"><span class="spin"></span>Reading project details</span></div> }
            @case ('done') { <div><span class="chip ok"><lucide-icon name="check"></lucide-icon>{{ filledText(d) }}</span></div> }
            @case ('failed') { <div><span class="chip bad" [title]="extraction(d).error || ''">Could not read details</span> <span class="small muted">{{ extraction(d).error }}</span></div> }
          }</td>
        <td style="white-space:nowrap">{{ shortDate(d.updatedAt) }}</td><td style="white-space:nowrap">{{ size(d.fileSize) }}</td>
        <td style="text-align:right;white-space:nowrap">
          @if (d.docType === 'completion_certificate' || d.docType === 'project_rfp') {
            <button class="ib" [disabled]="d.status !== 'ready' || extracting(d)" (click)="extract(d)" title="Read the project details from this document again" aria-label="Read details again"><lucide-icon name="sparkles"></lucide-icon></button>
          }
          <button class="ib" [disabled]="d.status !== 'ready'" (click)="view(d)" title="View" aria-label="View"><lucide-icon name="eye"></lucide-icon></button>
          <button class="ib" [disabled]="d.status !== 'ready'" (click)="download(d)" title="Download PDF" aria-label="Download PDF"><lucide-icon name="download"></lucide-icon></button>
          <button class="ib" style="color:var(--bad)" (click)="removeRelated(d)" title="Delete" aria-label="Delete"><lucide-icon name="trash-2"></lucide-icon></button>
        </td>
      </tr>
    </ng-template>`,
})
export class ProjectPage {
  private api = inject(Api);
  private router = inject(Router);
  readonly id = input.required<string>();
  labels = DOC_TYPE_LABELS;
  readingLabel = readingLabel;
  readingHint = READING_HINT;
  relatedTypes = RELATED_TYPES;
  docs = signal<LibraryDoc[]>([]);
  loaded = signal(false);
  saving = signal(false);
  message = signal('');
  messageWarn = signal(false);
  form: ProjectForm = this.emptyForm();
  formVersion = '';
  /** The form as last loaded from the server: Save writes only what differs from it. */
  private baseline: ProjectForm = this.emptyForm();
  dirty = false;
  tagInput = '';
  relType = 'completion_certificate';

  project = computed(() => this.docs().find(d => d.id === this.id()) ?? null);
  notFound = computed(() => this.loaded() && !this.project());
  children = computed(() => this.docs().filter(d => d.metadata['work_order_id'] === this.id()));
  related = computed(() => this.children().filter(d => d.docType !== 'project_rfp'));
  rfps = computed(() => this.children().filter(d => d.docType === 'project_rfp'));
  /** Experience documents not yet linked to a project, which can be attached to this one. */
  attachable = computed(() => {
    const parents = new Set(this.docs().map(d => String(d.metadata['work_order_id'] ?? '')));
    return this.docs().filter(d => d.id !== this.id() && !d.metadata['work_order_id'] && !parents.has(d.id));
  });
  /** Other projects with the PO number on this page (as saved, or as being typed). */
  samePo(): LibraryDoc[] {
    const key = poKey(this.form.po_number);
    return key ? this.docs().filter(d => d.id !== this.id() && !d.metadata['work_order_id'] && poKey(d.metadata['po_number']) === key) : [];
  }
  autoKeys = computed(() => ((this.project()?.metadata['_auto'] as string[] | undefined) ?? [])
    .filter(k => FIELD_LABEL[k]).map(k => FIELD_LABEL[k]));
  lastUpdated = computed(() => [this.project()!, ...this.children()].map(d => d.updatedAt).sort().at(-1)!);
  tagSuggestions = () => PROJECT_TAGS.filter(t => !this.form.tags.some(x => x.toLowerCase() === t.toLowerCase()));

  shortDate = (iso: string) => new Date(iso).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
  size = (bytes: number | null) => bytes === null ? '—'
    : bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  extraction = (d: LibraryDoc) => (d.metadata['extraction'] ?? {}) as { status?: string; error?: string; filled?: string[] };
  extracting = (d: LibraryDoc) => ['queued', 'running'].includes(this.extraction(d).status ?? '');
  filledText = (d: LibraryDoc) => {
    const f = this.extraction(d).filled ?? [];
    return f.length ? 'Filled: ' + f.map(k => FIELD_LABEL[k] ?? k).join(', ') : 'Nothing new to fill';
  };

  constructor() {
    const destroy = inject(DestroyRef);
    let tick = 0;
    timer(0, 4000).pipe(
      switchMap(() => {
        const busy = this.docs().some(d => d.status === 'uploaded' || d.status === 'processing'
          || ['queued', 'running'].includes(this.extraction(d).status ?? ''));
        if (this.loaded() && !busy && tick++ % 8 !== 0) return EMPTY;
        return this.api.library('experience').pipe(catchError(() => EMPTY));
      }),
      takeUntilDestroyed(destroy),
    ).subscribe(list => { this.docs.set(list); this.loaded.set(true); this.syncForm(); });
    // the "possible duplicate" links open another project in this same page: start that one afresh
    effect(() => { this.id(); untracked(() => { this.dirty = false; this.formVersion = ''; this.message.set(''); this.syncForm(); }); });
  }

  /** Load the form from the server copy, unless the person has started editing it. */
  private syncForm(): void {
    const p = this.project();
    if (!p || this.dirty || p.updatedAt === this.formVersion) return;
    const m = p.metadata;
    const s = (k: string) => (m[k] === undefined || m[k] === null ? '' : String(m[k]));
    this.form = {
      short_name: s('short_name'), client: s('client'), project: s('project'), ministry: s('ministry'), scope: s('scope'),
      value_cr: s('value_cr'), po_number: s('po_number'), po_date: s('po_date'), duration: s('duration'), completion_date: s('completion_date'),
      tags: Array.isArray(m['domains']) ? (m['domains'] as unknown[]).map(String) : [],
    };
    this.baseline = { ...this.form, tags: [...this.form.tags] };
    this.formVersion = p.updatedAt;
  }

  private emptyForm(): ProjectForm {
    return { short_name: '', client: '', project: '', ministry: '', scope: '', value_cr: '', po_number: '', po_date: '', duration: '', completion_date: '', tags: [] };
  }

  addTag(): void {
    const t = this.tagInput.replace(/,/g, '').trim();
    if (t && !this.form.tags.some(x => x.toLowerCase() === t.toLowerCase())) { this.form.tags = [...this.form.tags, t]; this.dirty = true; }
    this.tagInput = '';
  }
  removeTag(t: string): void { this.form.tags = this.form.tags.filter(x => x !== t); this.dirty = true; }

  save(p: LibraryDoc): void {
    const raw = this.form.value_cr as unknown;           // a cleared number input gives null, not ''
    const value = raw == null || raw === '' ? null : Number(raw);
    if (value !== null && (!isFinite(value) || value < 0)) { this.say('Project value must be a number in crore, for example 12.6', true); return; }
    // Only what was changed on this page is written, onto the server's current copy: details read from the PO or
    // RFP while the page was open are kept.
    const norm = (v: unknown) => Array.isArray(v) ? JSON.stringify(v) : String(v ?? '').trim();
    const changed = (k: keyof ProjectForm) => norm(this.form[k]) !== norm(this.baseline[k]);
    const { _auto, ...metadata } = p.metadata as Record<string, unknown>;
    for (const k of TEXT_KEYS) if (changed(k)) { const v = String(this.form[k] ?? '').trim(); if (v) metadata[k] = v; else delete metadata[k]; }
    if (changed('value_cr')) { if (value === null) delete metadata['value_cr']; else metadata['value_cr'] = value; }
    if (changed('tags')) metadata['domains'] = this.form.tags;
    // the matcher and the drafter read end_year, completed and period
    const po = String(metadata['po_date'] ?? ''), done = String(metadata['completion_date'] ?? '');
    if (done) {
      metadata['end_year'] = Number(done.slice(0, 4));
      metadata['completed'] = done <= new Date().toISOString().slice(0, 10);
    } else if (changed('completion_date')) {
      delete metadata['end_year']; delete metadata['completed'];
    }
    if (po || done) {
      const f = (iso: string) => iso ? new Date(iso + 'T00:00:00').toLocaleDateString('en-GB', { month: 'short', year: 'numeric' }) : 'ongoing';
      metadata['period'] = `${f(po)} – ${f(done)}`;
    } else delete metadata['period'];
    // automatic values that arrived after the page was loaded have not been seen yet: keep them marked
    const shown: Record<string, unknown> = { ...this.baseline, domains: this.baseline.tags };
    const unseen = ((_auto as string[] | undefined) ?? []).filter(k => k in shown && norm(p.metadata[k]) !== norm(shown[k])
      && !(k === 'domains' ? changed('tags') : changed(k as keyof ProjectForm)));
    if (unseen.length) metadata['_auto'] = unseen;
    this.saving.set(true);
    this.api.updateDoc(p.id, { title: p.title, category: p.category, docType: 'work_order', sensitivity: p.sensitivity, validUntil: p.validUntil, metadata })
      .subscribe({
        next: u => { this.docs.update(l => l.map(x => x.id === u.id ? u : x)); this.dirty = false; this.formVersion = ''; this.syncForm(); this.saving.set(false); this.say('Project saved.'); },
        error: e => { this.saving.set(false); this.say(errorText(e), true); },
      });
  }

  uploadRelated(input: HTMLInputElement, docType: string): void {
    const files = [...(input.files ?? [])]; input.value = '';
    const p = this.project();
    if (!files.length || !p) return;
    this.say(`Uploading ${files.length} file(s)…`);
    this.api.upload(files, 'experience', p.sensitivity, { docType, parentId: p.id }).subscribe({
      next: r => {
        const created = r.created;
        this.docs.update(l => [...created, ...l]);
        const added = !created.length ? '' : docType === 'project_rfp'
          ? 'RFP added. It is read on this server first; the project details are then filled from it. '
          : `${created.length} document(s) added. `;
        this.say((added + duplicateNote(r)).trim(), !created.length);
      },
      error: e => this.say(errorText(e), true),
    });
  }

  attach(id: string): void {
    const d = this.docs().find(x => x.id === id);
    if (!d) return;
    this.api.updateDoc(d.id, { title: d.title, category: d.category, docType: d.docType === 'work_order' ? 'other' : d.docType,
      sensitivity: d.sensitivity, validUntil: d.validUntil, metadata: { ...d.metadata, work_order_id: this.id() } })
      .subscribe({ next: u => { this.docs.update(l => l.map(x => x.id === u.id ? u : x)); this.say(`“${d.title}” attached.`); }, error: e => this.say(errorText(e), true) });
  }

  extract(d: LibraryDoc): void {
    this.api.extractProject(d.id).subscribe({
      next: () => this.docs.update(l => l.map(x => x.id === d.id ? { ...x, metadata: { ...x.metadata, extraction: { status: 'queued' } } } : x)),
      error: e => this.say(errorText(e), true),
    });
  }

  view(d: LibraryDoc): void { this.api.docFile(d.id, true).subscribe(openBlob); }
  download(d: LibraryDoc): void {
    this.api.docFile(d.id, true).subscribe({
      next: blob => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a'); a.href = url; a.download = `${d.title}.pdf`; a.click();
        setTimeout(() => URL.revokeObjectURL(url), 60_000);
      },
      error: e => this.say(errorText(e), true),
    });
  }

  reread(d: LibraryDoc): void {
    this.api.reprocess(d.id).subscribe({
      next: () => { this.docs.update(l => l.map(x => x.id === d.id ? { ...x, status: 'uploaded' as const, error: null } : x)); this.say(''); },
      error: e => this.say(errorText(e), true),
    });
  }

  removeRelated(d: LibraryDoc): void {
    if (!confirm(`Delete “${d.title}”? It will also be removed from any bid drafts.`)) return;
    this.api.deleteDoc(d.id).subscribe({ next: () => this.docs.update(l => l.filter(x => x.id !== d.id)), error: e => this.say(errorText(e), true) });
  }

  removeProject(p: LibraryDoc): void {
    const n = this.children().length;
    if (!confirm(`Delete the project “${this.form.short_name || p.title}”${n ? ` and its ${n} related document(s)` : ''}? It will also be removed from any bid drafts.`)) return;
    this.api.deleteDoc(p.id).subscribe({
      next: () => this.router.navigate(['/library'], { queryParams: { tab: 'experience' } }),
      error: e => this.say(errorText(e), true),
    });
  }

  private say(text: string, warn = false): void { this.message.set(text); this.messageWarn.set(warn); }
}
