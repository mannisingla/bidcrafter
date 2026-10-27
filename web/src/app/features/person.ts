import { Component, DestroyRef, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { EMPTY, catchError, switchMap, timer } from 'rxjs';
import { Api, errorText, openBlob } from '../core/api';
import { Icons } from '../core/icons';
import { DOC_TYPE_LABELS, LibraryDoc, READING_HINT, duplicateNote, parentOf, readingLabel, samePersonReason } from '../core/models';

interface PersonForm { name: string; employee_code: string; role: string; total_years: string; qualification: string; skills: string[]; certifications: string[]; }
const TEXT_KEYS = ['name', 'employee_code', 'role', 'qualification'] as const;
const FIELD_LABEL: Record<string, string> = {
  name: 'name', role: 'proposed role', total_years: 'experience', qualification: 'qualifications', skills: 'skills',
  certifications: 'certifications',
};
const CERT_SUGGESTIONS = ['PMP', 'PRINCE2', 'ITIL v4', 'CISA', 'CISSP', 'CISM', 'CEH', 'CCNA', 'CCNP', 'AWS Solutions Architect',
  'Azure Administrator', 'Google Cloud Architect', 'Oracle Certified Professional', 'Scrum Master (CSM)', 'Six Sigma Green Belt',
  'ISO 27001 Lead Auditor', 'ISTQB'];

/** One candidate in the HR library: the details read from their CV, a UTL code, and their certificates. */
@Component({
  selector: 'bc-person',
  imports: [FormsModule, Icons, RouterLink],
  template: `
    <div class="wrap" style="max-width:1100px">
      <a class="small" routerLink="/library" [queryParams]="{ tab: 'hr' }"><lucide-icon name="chevron-left"></lucide-icon>Human resources</a>
      @if (person(); as p) {
        <div class="row between wrapx" style="margin:8px 0 14px">
          <div><h1 class="v-title">{{ form.name || p.title }}</h1>
            <p class="sub">CV: {{ p.originalName }}{{ form.employee_code ? ' · UTL code ' + form.employee_code : '' }} · Updated {{ shortDate(p.updatedAt) }}</p></div>
          <div class="row wrapx">
            <button class="btn sm" [disabled]="p.status !== 'ready'" (click)="view(p)"><lucide-icon name="eye"></lucide-icon>View CV</button>
            <button class="btn sm" [disabled]="p.status !== 'ready'" (click)="download(p)"><lucide-icon name="download"></lucide-icon>Download</button>
            <button class="btn sm" [disabled]="p.status !== 'ready' || extracting(p)" (click)="extract(p)" title="Fill empty and automatically filled fields from the CV"><lucide-icon name="sparkles"></lucide-icon>Read details from CV</button>
            <button class="btn sm quiet" style="color:var(--bad)" (click)="removePerson(p)"><lucide-icon name="trash-2"></lucide-icon>Delete</button>
            <button class="btn primary" (click)="save(p)" [disabled]="saving()">{{ saving() ? 'Saving…' : 'Save' }}</button>
          </div>
        </div>
        @if (message()) { <div class="notice" [class.warn]="messageWarn()"><lucide-icon name="info"></lucide-icon><div>{{ message() }}</div></div> }
        @if (p.status === 'failed') { <div class="notice warn"><lucide-icon name="info"></lucide-icon><div>The CV could not be read: {{ p.error || 'unknown error' }}
          <button class="btn sm" style="margin-left:8px" (click)="reread(p)">Read again</button></div></div> }
        @else if (p.status !== 'ready') { <div class="notice" [title]="readingHint"><span class="spin"></span><div>{{ readingLabel(p) }}. The CV is read on this server; scanned pages take about 10 seconds each.</div></div> }
        @switch (extraction(p).status) {
          @case ('queued') { <div class="notice"><span class="spin"></span><div>Reading the details from the CV…</div></div> }
          @case ('running') { <div class="notice"><span class="spin"></span><div>Reading the details from the CV…</div></div> }
          @case ('failed') { <div class="notice warn"><lucide-icon name="info"></lucide-icon><div>The details could not be read from the CV: {{ extraction(p).error }}</div></div> }
        }
        @if (samePeople().length) {
          <div class="notice warn"><lucide-icon name="flag"></lucide-icon><div>Probable duplicate:
            @for (x of samePeople(); track x.doc.id; let last = $last) { <a [routerLink]="['/library/people', x.doc.id]">{{ x.doc.metadata['name'] || x.doc.title }}</a> ({{ x.reason }}){{ last ? '' : ', ' }} }.
            If it is the same person, keep one and delete the other.</div></div>
        }
        @if (autoKeys().length) { <div class="notice warn"><lucide-icon name="info"></lucide-icon><div>Filled automatically, please check and save: {{ autoKeys().join(', ') }}.</div></div> }

        <div class="card" (input)="dirty = true" (change)="dirty = true">
          <h3>Candidate details</h3>
          <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:0 16px;margin-top:12px">
            <div class="field"><label>Name</label><input class="inp" [(ngModel)]="form.name"></div>
            <div class="field"><label>UTL code</label><input class="inp" [(ngModel)]="form.employee_code" placeholder="Employee code"></div>
            <div class="field"><label>Proposed role</label><input class="inp" [(ngModel)]="form.role" placeholder="e.g. Project Manager"></div>
            <div class="field"><label>Total experience (years)</label><input class="inp" type="number" step="any" min="0" [(ngModel)]="form.total_years"></div>
          </div>
          <div class="field"><label>Highest qualifications (up to two)</label><input class="inp" [(ngModel)]="form.qualification" placeholder="e.g. MBA, B.Tech"></div>
          <div class="field"><label>Skills</label>
            <div class="tags" style="align-items:center;gap:6px">
              @for (t of form.skills; track t) { <span class="chip">{{ t }}<button class="ib" style="width:18px;height:18px" (click)="removeChip('skills', t)" [attr.aria-label]="'Remove ' + t"><lucide-icon name="x"></lucide-icon></button></span> }
              <input class="inp" style="width:240px" [(ngModel)]="chipInput.skills" (keydown.enter)="addChip('skills')" (change)="addChip('skills')" placeholder="Add a skill (Java, Oracle…)">
            </div>
          </div>
          <div class="field"><label>Certifications</label>
            <div class="tags" style="align-items:center;gap:6px">
              @for (t of form.certifications; track t) { <span class="chip">{{ t }}<button class="ib" style="width:18px;height:18px" (click)="removeChip('certifications', t)" [attr.aria-label]="'Remove ' + t"><lucide-icon name="x"></lucide-icon></button></span> }
              <input class="inp" style="width:240px" list="cert-names" [(ngModel)]="chipInput.certifications" (keydown.enter)="addChip('certifications')" (change)="addChip('certifications')" placeholder="Add a certification (PMP, ITIL…)">
            </div>
          </div>
        </div>

        <div class="section-h"><h2>Certificates</h2>
          <div class="row wrapx">
            <input class="inp" style="width:220px" list="cert-names" [(ngModel)]="certName" placeholder="Certificate name, e.g. PMP">
            <datalist id="cert-names">@for (c of certSuggestions; track c) { <option [value]="c"></option> }</datalist>
            <button class="btn sm primary" (click)="certPicker.click()"><lucide-icon name="upload"></lucide-icon>Upload certificate</button>
            <input #certPicker type="file" hidden (change)="uploadCertificate(certPicker)">
            @if (attachable().length) {
              <select class="inp" style="width:230px" [ngModel]="''" (ngModelChange)="attach($event)" aria-label="Attach a certificate already in the library">
                <option value="">Attach from library…</option>@for (d of attachable(); track d.id) { <option [value]="d.id">{{ d.title }}</option> }</select>
            }
          </div>
        </div>
        <div class="card" style="padding:6px 8px">
          @if (certificates().length) {
            <div class="tbl-wrap"><table class="tbl"><tr><th>Certificate</th><th>File</th><th>Updated</th><th>Size</th><th></th></tr>
              @for (d of certificates(); track d.id) {
                <tr>
                  <td class="t1">{{ d.title }}@if (d.status === 'failed') { <div><span class="chip bad" [title]="d.error || ''">Could not read</span></div> } @else if (d.status !== 'ready') { <div><span class="chip blue" [title]="readingHint"><span class="spin"></span>{{ readingLabel(d) }}</span></div> }</td>
                  <td class="muted small">{{ d.originalName }}</td>
                  <td style="white-space:nowrap">{{ shortDate(d.updatedAt) }}</td><td style="white-space:nowrap">{{ size(d.fileSize) }}</td>
                  <td style="text-align:right;white-space:nowrap">
                    <button class="ib" [disabled]="d.status !== 'ready'" (click)="view(d)" title="View" aria-label="View"><lucide-icon name="eye"></lucide-icon></button>
                    <button class="ib" [disabled]="d.status !== 'ready'" (click)="download(d)" title="Download PDF" aria-label="Download PDF"><lucide-icon name="download"></lucide-icon></button>
                    <button class="ib" style="color:var(--bad)" (click)="removeCertificate(d)" title="Delete" aria-label="Delete"><lucide-icon name="trash-2"></lucide-icon></button>
                  </td>
                </tr>
              }
            </table></div>
          } @else { <p class="muted small" style="padding:10px 12px">Upload the candidate's certificates (PMP, ITIL, CISA…). Each one is added to the certifications above under the name you give it.</p> }
        </div>
      } @else if (notFound()) {
        <div class="notice warn" style="margin-top:14px"><lucide-icon name="info"></lucide-icon><div>This candidate no longer exists.</div></div>
      } @else { <p class="muted" style="margin-top:14px">Loading…</p> }
    </div>`,
})
export class PersonPage {
  private api = inject(Api);
  private router = inject(Router);
  readonly id = input.required<string>();
  labels = DOC_TYPE_LABELS;
  readingLabel = readingLabel;
  readingHint = READING_HINT;
  certSuggestions = CERT_SUGGESTIONS;
  docs = signal<LibraryDoc[]>([]);
  loaded = signal(false);
  saving = signal(false);
  message = signal('');
  messageWarn = signal(false);
  form: PersonForm = { name: '', employee_code: '', role: '', total_years: '', qualification: '', skills: [], certifications: [] };
  formVersion = '';
  /** The form as last loaded from the server: Save writes only what differs from it. */
  private baseline: PersonForm = { name: '', employee_code: '', role: '', total_years: '', qualification: '', skills: [], certifications: [] };
  dirty = false;
  chipInput: Record<'skills' | 'certifications', string> = { skills: '', certifications: '' };
  certName = '';

  person = computed(() => this.docs().find(d => d.id === this.id()) ?? null);
  notFound = computed(() => this.loaded() && !this.person());
  certificates = computed(() => this.docs().filter(d => d.metadata['cv_id'] === this.id()));
  /** HR documents that are not CVs and not yet linked to anyone, which can be attached as certificates. */
  attachable = computed(() => this.docs().filter(d => d.id !== this.id() && d.docType !== 'cv' && !parentOf(d)));
  autoKeys = computed(() => ((this.person()?.metadata['_auto'] as string[] | undefined) ?? []).filter(k => FIELD_LABEL[k]).map(k => FIELD_LABEL[k]));

  shortDate = (iso: string) => new Date(iso).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
  size = (bytes: number | null) => bytes === null ? '—'
    : bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  extraction = (d: LibraryDoc) => (d.metadata['extraction'] ?? {}) as { status?: string; error?: string };
  extracting = (d: LibraryDoc) => ['queued', 'running'].includes(this.extraction(d).status ?? '');

  constructor() {
    const destroy = inject(DestroyRef);
    let tick = 0;
    timer(0, 4000).pipe(
      switchMap(() => {
        const busy = this.docs().some(d => d.status === 'uploaded' || d.status === 'processing' || this.extracting(d));
        if (this.loaded() && !busy && tick++ % 8 !== 0) return EMPTY;
        return this.api.library('hr').pipe(catchError(() => EMPTY));
      }),
      takeUntilDestroyed(destroy),
    ).subscribe(list => { this.docs.set(list); this.loaded.set(true); this.syncForm(); });
    // the "probable duplicate" links open another candidate in this same page: start that one afresh
    effect(() => { this.id(); untracked(() => { this.dirty = false; this.formVersion = ''; this.message.set(''); this.syncForm(); }); });
  }

  /** Other candidates who are probably the same person, judged on the details as typed on this page. */
  samePeople(): { doc: LibraryDoc; reason: string }[] {
    const p = this.person();
    if (!p) return [];
    const draft = { ...p, metadata: { ...p.metadata, name: this.form.name, employee_code: this.form.employee_code } };
    return this.docs().filter(d => d.id !== p.id && d.docType === 'cv' && !parentOf(d))
      .map(d => ({ doc: d, reason: samePersonReason(draft, d) })).filter(x => x.reason);
  }

  /** Load the form from the server copy, unless the person has started editing it. */
  private syncForm(): void {
    const p = this.person();
    if (!p || this.dirty || p.updatedAt === this.formVersion) return;
    const m = p.metadata;
    const s = (k: string) => (m[k] === undefined || m[k] === null ? '' : String(m[k]));
    const list = (k: string) => (Array.isArray(m[k]) ? (m[k] as unknown[]).map(String) : []);
    this.form = { name: s('name'), employee_code: s('employee_code'), role: s('role'), total_years: s('total_years'),
      qualification: s('qualification'), skills: list('skills'), certifications: list('certifications') };
    this.baseline = { ...this.form, skills: [...this.form.skills], certifications: [...this.form.certifications] };
    this.formVersion = p.updatedAt;
  }

  addChip(key: 'skills' | 'certifications'): void {
    const t = this.chipInput[key].replace(/,/g, '').trim();
    if (t && !this.form[key].some(x => x.toLowerCase() === t.toLowerCase())) { this.form[key] = [...this.form[key], t]; this.dirty = true; }
    this.chipInput[key] = '';
  }
  removeChip(key: 'skills' | 'certifications', t: string): void { this.form[key] = this.form[key].filter(x => x !== t); this.dirty = true; }

  save(p: LibraryDoc, note = 'Candidate saved.'): void {
    const raw = this.form.total_years as unknown;        // a cleared number input gives null, not ''
    const years = raw == null || raw === '' ? null : Number(raw);
    if (years !== null && (!isFinite(years) || years < 0)) { this.say('Total experience must be a number of years, for example 7.5', true); return; }
    // Only what was changed on this page is written, onto the server's current copy: details read from the CV
    // while the page was open are kept.
    const norm = (v: unknown) => Array.isArray(v) ? JSON.stringify(v) : String(v ?? '').trim();
    const changed = (k: keyof PersonForm) => norm(this.form[k]) !== norm(this.baseline[k]);
    const { _auto, ...metadata } = p.metadata as Record<string, unknown>;
    for (const k of TEXT_KEYS) if (changed(k)) { const v = String(this.form[k] ?? '').trim(); if (v) metadata[k] = v; else delete metadata[k]; }
    if (changed('total_years')) { if (years === null) delete metadata['total_years']; else metadata['total_years'] = years; }
    if (changed('skills')) metadata['skills'] = this.form.skills;
    if (changed('certifications')) metadata['certifications'] = this.form.certifications;
    // automatic values that arrived after the page was loaded have not been seen yet: keep them marked
    const shown = this.baseline as unknown as Record<string, unknown>;
    const unseen = ((_auto as string[] | undefined) ?? []).filter(k => k in shown && norm(p.metadata[k]) !== norm(shown[k])
      && !changed(k as keyof PersonForm));
    if (unseen.length) metadata['_auto'] = unseen;
    this.saving.set(true);
    this.api.updateDoc(p.id, { title: p.title, category: p.category, docType: 'cv', sensitivity: p.sensitivity, validUntil: p.validUntil, metadata })
      .subscribe({
        next: u => { this.docs.update(l => l.map(x => x.id === u.id ? u : x)); this.dirty = false; this.formVersion = ''; this.syncForm(); this.saving.set(false); this.say(note); },
        error: e => { this.saving.set(false); this.say(errorText(e), true); },
      });
  }

  uploadCertificate(input: HTMLInputElement): void {
    const file = input.files?.[0]; input.value = '';
    const p = this.person();
    if (!file || !p) return;
    const name = this.certName.trim();
    this.say('Uploading the certificate…');
    this.api.upload([file], 'hr', p.sensitivity, { docType: 'certificate', parentId: p.id, title: name || undefined }).subscribe({
      next: r => {
        this.docs.update(l => [...r.created, ...l]);
        this.certName = '';
        if (r.created.length && name && !this.form.certifications.some(x => x.toLowerCase() === name.toLowerCase())) {
          // the certificate's name joins the candidate's certifications (saved with the other details on the page)
          this.form.certifications = [...this.form.certifications, name];
          this.save(p, `Certificate “${name}” added.`);
        } else this.say((r.created.length ? 'Certificate added. ' : '') + duplicateNote(r), !r.created.length);
      },
      error: e => this.say(errorText(e), true),
    });
  }

  attach(id: string): void {
    const d = this.docs().find(x => x.id === id);
    if (!d) return;
    this.api.updateDoc(d.id, { title: d.title, category: d.category, docType: d.docType === 'other' ? 'certificate' : d.docType,
      sensitivity: d.sensitivity, validUntil: d.validUntil, metadata: { ...d.metadata, cv_id: this.id() } })
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

  removeCertificate(d: LibraryDoc): void {
    if (!confirm(`Delete the certificate “${d.title}”? It will also be removed from any bid drafts.`)) return;
    this.api.deleteDoc(d.id).subscribe({ next: () => this.docs.update(l => l.filter(x => x.id !== d.id)), error: e => this.say(errorText(e), true) });
  }

  removePerson(p: LibraryDoc): void {
    const n = this.certificates().length;
    if (!confirm(`Delete “${this.form.name || p.title}”${n ? ` and their ${n} certificate(s)` : ''}? They will also be removed from any bid drafts.`)) return;
    this.api.deleteDoc(p.id).subscribe({
      next: () => this.router.navigate(['/library'], { queryParams: { tab: 'hr' } }),
      error: e => this.say(errorText(e), true),
    });
  }

  private say(text: string, warn = false): void { this.message.set(text); this.messageWarn.set(warn); }
}
