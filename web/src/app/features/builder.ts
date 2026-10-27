import { Component, DestroyRef, ElementRef, OnInit, computed, effect, inject, input, signal, viewChild } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { NgTemplateOutlet } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { EMPTY, catchError, firstValueFrom, forkJoin, switchMap, takeWhile, timer } from 'rxjs';
import { Api, errorText, openBlob } from '../core/api';
import { CurrentBid } from '../core/current-bid';
import { Icons } from '../core/icons';
import { BidDetail, CATEGORIES, Category, LibraryDoc, PageMap, Section, bidOf } from '../core/models';
import { PdfPage, PdfService } from '../core/pdf';
import { Toast } from '../core/toast';

/** 'bid': the documents uploaded for this bid only (EMD, power of attorney…), apart from the library. */
type Group = Category | 'bid';
const ICON: Record<string, string> = { bid: 'folder', experience: 'briefcase', statutory: 'shield-check', hr: 'user', literature: 'book-open', uploads: 'file' };
const LIB_ORDER: Group[] = ['bid', 'experience', 'statutory', 'hr', 'literature', 'uploads'];

/** Bid builder, laid out like the prototype: outline, editor and library panel. */
@Component({
  selector: 'bc-builder',
  imports: [FormsModule, RouterLink, Icons, PdfPage, NgTemplateOutlet],
  template: `
    @if (detail(); as d) {
      <div class="builder">
        <div class="b-col b-out">
          <div class="col-h">
            <div class="row between"><h3>Bid outline</h3>
              <div class="row" style="gap:2px">
                <button class="ib" title="Add text section" (click)="add('text')"><lucide-icon name="file-plus"></lucide-icon></button>
                <button class="ib" title="Add annexure" (click)="add('docs')"><lucide-icon name="folder-plus"></lucide-icon></button>
              </div></div>
            <div class="muted small">{{ map() ? map()!.pages.length + ' pages, ' : '' }}{{ included() }} sections</div>
            <div class="muted small" style="margin-top:4px">Edit how the bid reads: text, order and extra pages. The documents that answer each criterion are chosen on <a [routerLink]="['/bids', id(), 'response']">Bid response</a>.</div>
          </div>
          @if (gaps().length) {
            <div class="gap-box"><b>{{ gaps().length }} eligibility gap{{ gaps().length > 1 ? 's' : '' }}.</b> {{ gapText() }}
              <a class="btn sm" style="margin-top:6px" [routerLink]="['/bids', d.bid.id, 'response']"><lucide-icon name="flag"></lucide-icon>See eligibility</a></div>
          }
          @if (fills()) { <div class="gap-box" style="background:var(--warn-soft)"><b style="color:var(--warn)">{{ fills() }} fields to fill</b>, highlighted in yellow in the draft.</div> }
          <div style="padding:8px 0 20px">
            @for (s of sections(); track s.id) {
              <div class="oi" [class.sel]="s.id === selId()" [class.off]="!s.include" [class.over]="over() === s.id" draggable="true"
                   tabindex="0" (click)="select(s.id)" (keydown.enter)="select(s.id)" (dragstart)="dragSection($event, s.id)" (dragover)="$event.preventDefault(); over.set(s.id)"
                   (dragleave)="over.set(null)" (drop)="drop($event, s)">
                <span class="grip" title="Drag to reorder"><lucide-icon name="grip-vertical"></lucide-icon></span>
                <span class="k"><lucide-icon [name]="kindIcon(s.kind)"></lucide-icon></span>
                <span class="ttl" [title]="s.title">{{ s.title }}</span>
                <span class="pc">{{ s.include ? pageRange(s.id) : 'off' }}</span>
              </div>
            }
          </div>
        </div>

        <div class="b-mid" #mid>
          @if (selected(); as s) {
            @switch (s.kind) {
              @case ('text') {
                <div class="ed-bar">
                  <input class="ed-title" [ngModel]="s.title" (ngModelChange)="s.title = $event; dirty.add(s.id)" (change)="save(s)" aria-label="Section title">
                  <button class="ib" title="Bold" (mousedown)="cmd($event, 'bold')"><lucide-icon name="bold"></lucide-icon></button>
                  <button class="ib" title="Italic" (mousedown)="cmd($event, 'italic')"><lucide-icon name="italic"></lucide-icon></button>
                  <button class="ib" title="Underline" (mousedown)="cmd($event, 'underline')"><lucide-icon name="underline"></lucide-icon></button>
                  <button class="ib" title="Heading" (mousedown)="cmd($event, 'formatBlock', 'h3')"><lucide-icon name="heading"></lucide-icon></button>
                  <button class="ib" title="Bulleted list" (mousedown)="cmd($event, 'insertUnorderedList')"><lucide-icon name="list"></lucide-icon></button>
                  <button class="ib" title="Numbered list" (mousedown)="cmd($event, 'insertOrderedList')"><lucide-icon name="list-ordered"></lucide-icon></button>
                  <button class="ib" title="Insert fill-in field" (mousedown)="cmd($event, 'insertHTML', fillField)"><lucide-icon name="text-cursor-input"></lucide-icon></button>
                  <span class="sep"></span>
                  <button class="btn sm" (click)="openRedraft(s)" [disabled]="!!redrafting()"><lucide-icon name="sparkles"></lucide-icon>Redraft</button>
                  <button class="btn sm quiet" (click)="litOpen.set(true)"><lucide-icon name="book-open"></lucide-icon>Insert literature</button>
                  <span class="sep"></span>
                  <ng-container *ngTemplateOutlet="common; context: { $implicit: s }"></ng-container>
                </div>
                @if (redrafting(); as r) {
                  <div class="notice job-note"><lucide-icon name="sparkles"></lucide-icon>
                    <div>Redrafting “{{ r.title }}”… the text updates when it is ready.</div></div>
                }
                @if (s.sources.length) { <div class="src"><lucide-icon name="sparkles"></lucide-icon>Drafted from @for (x of s.sources; track $index) { <span class="tag">{{ x }}</span> }</div> }
                <div class="edit-paper paper-typo" #editor contenteditable="true" spellcheck="true" (input)="edited(s)" (blur)="save(s)"></div>
              }
              @case ('docs') {
                <div class="ed-bar">
                  <input class="ed-title" [ngModel]="s.title" (ngModelChange)="s.title = $event; dirty.add(s.id)" (change)="save(s)" aria-label="Section title">
                  <button class="btn sm" (click)="openPicker(s)"><lucide-icon name="library"></lucide-icon>Add from library</button>
                  <button class="btn sm" (click)="device.click()"><lucide-icon name="upload"></lucide-icon>Upload from device</button>
                  <input #device type="file" multiple hidden (change)="uploadFromDevice(s, device)">
                  <span class="sep"></span>
                  <ng-container *ngTemplateOutlet="common; context: { $implicit: s }"></ng-container>
                </div>
                <p class="src">Documents print in this order. Every page gets the header, page number, signature and stamp you set in Preview.</p>
                <div class="doc-cards">
                  @for (doc of s.documents; track doc.id; let k = $index) {
                    <div class="dcard">
                      <div class="th" (click)="viewDoc(doc.id)" style="cursor:zoom-in"><div class="thumbbox"><bc-pdf-page [doc]="docPdf(doc.id, doc.status)" [page]="1" [width]="190" /></div></div>
                      <b>{{ doc.title }}</b>
                      <span class="muted small">{{ doc.pageCount ?? '…' }} page{{ doc.pageCount === 1 ? '' : 's' }}@if (expiring(doc.validUntil)) { <span class="chip warn" style="margin-left:4px">Expiring</span> }@if (doc.status !== 'ready') { <span class="chip blue" style="margin-left:4px"><span class="spin"></span>Reading</span> }</span>
                      <div class="acts">
                        <button class="ib" title="Move left" (click)="moveDoc(s, k, -1)"><lucide-icon name="arrow-up"></lucide-icon></button>
                        <button class="ib" title="Move right" (click)="moveDoc(s, k, 1)"><lucide-icon name="arrow-down"></lucide-icon></button>
                        <button class="ib" title="Remove from bid" (click)="removeDoc(s, doc.id)"><lucide-icon name="x"></lucide-icon></button>
                      </div>
                    </div>
                  }
                  <div class="dcard add" (click)="openPicker(s)"><lucide-icon name="plus"></lucide-icon>Add documents</div>
                </div>
              }
              @default {
                <div class="ed-bar"><b style="flex:1;padding:0 8px">{{ s.title }}</b><ng-container *ngTemplateOutlet="common; context: { $implicit: s }"></ng-container></div>
                <p class="src">Generated automatically. {{ s.kind === 'index' ? 'Page numbers update as you edit and reorder sections.' : 'Built from the RFP title, reference and department.' }}</p>
                <div style="display:flex;justify-content:center;padding:14px">
                  @if (map() && sectionStart(s.id) !== null) {
                    <div class="mini-host"><bc-pdf-page [doc]="rawDoc()" [page]="sectionStart(s.id)! + 1" [width]="595" /></div>
                  } @else { <p class="muted">Open Preview &amp; sign to build the pages; this page is shown here afterwards.</p> }
                </div>
              }
            }
          }
        </div>

        <div class="b-col b-lib" [class.show]="libOpen()">
          <div class="col-h"><div class="row between"><h3>Library</h3><button class="ib drawer-btn" (click)="libOpen.set(false)"><lucide-icon name="x"></lucide-icon></button></div>
            <div class="muted small">Drag onto the outline, or use + to add to the selected section.</div></div>
          @for (cat of libOrder; track cat) {
            @if (byCat(cat).length) {
              <div class="group-l">{{ catLabel(cat) }}</div>
              @for (d of byCat(cat); track d.id) {
                <div class="li" draggable="true" (dragstart)="dragDoc($event, d.id)">
                  <span class="k"><lucide-icon [name]="icon[cat]"></lucide-icon></span>
                  <span class="tx"><b>{{ d.title }}</b><span>{{ cat === 'literature' ? 'Insert into text' : (d.pageCount ?? 1) + ' page' + ((d.pageCount ?? 1) > 1 ? 's' : '') }}</span></span>
                  <button class="ib" title="Add to selected section" (click)="attach(d, selId())"><lucide-icon name="plus"></lucide-icon></button>
                </div>
              }
            }
          }
        </div>
      </div>

      <ng-template #common let-s>
        <button class="ib" title="Move up" (click)="move(s, -1)"><lucide-icon name="arrow-up"></lucide-icon></button>
        <button class="ib" title="Move down" (click)="move(s, 1)"><lucide-icon name="arrow-down"></lucide-icon></button>
        <label class="row small" style="gap:5px;margin:0 6px;font-weight:600"><input type="checkbox" [checked]="s.include" (change)="toggle(s)">Include</label>
        @if (s.kind === 'text' || s.kind === 'docs') { <button class="ib" title="Delete section" (click)="remove(s)"><lucide-icon name="trash-2"></lucide-icon></button> }
        <span class="sep"></span>
        <button class="btn sm drawer-btn" (click)="libOpen.set(!libOpen())"><lucide-icon name="library"></lucide-icon>Library</button>
        <a class="btn sm primary" [routerLink]="['/bids', d.bid.id, 'preview']" (click)="flush()"><lucide-icon name="eye"></lucide-icon>Preview</a>
      </ng-template>

      @if (picker(); as p) {
        <div class="modal-bg open" (click)="picker.set(null)">
          <div class="modal" (click)="$event.stopPropagation()">
            <div class="modal-h"><div><h3>Add from library</h3><div class="muted small">To {{ p.title }}</div></div><button class="ib" (click)="picker.set(null)"><lucide-icon name="x"></lucide-icon></button></div>
            <div class="modal-b" style="padding-top:6px">
              <div class="tabs">@for (c of pickCats; track c) { <button [class.on]="pickTab() === c" (click)="pickTab.set(c)">{{ catLabel(c) }}<span class="count">{{ byCat(c).length }}</span></button> }</div>
              @for (d of byCat(pickTab()); track d.id) {
                <label class="pick"><input type="checkbox" [checked]="inSection(p, d.id) || chosen().has(d.id)" [disabled]="inSection(p, d.id)" (change)="choose(d.id)">
                  <span style="flex:1"><b>{{ d.title }}</b><div class="muted small">{{ d.pageCount ?? 1 }} page{{ (d.pageCount ?? 1) > 1 ? 's' : '' }}{{ inSection(p, d.id) ? ', already added' : '' }}</div></span>
                  @if (expiring(d.validUntil)) { <span class="chip warn">Expiring</span> }</label>
              } @empty { <p class="muted">Nothing here yet.</p> }
            </div>
            <div class="modal-f"><button class="btn" (click)="picker.set(null)">Cancel</button><button class="btn primary" (click)="addChosen(p)">Add selected</button></div>
          </div>
        </div>
      }

      @if (litOpen()) {
        <div class="modal-bg open" (click)="litOpen.set(false)">
          <div class="modal" (click)="$event.stopPropagation()">
            <div class="modal-h"><h3>Insert bid literature</h3><button class="ib" (click)="litOpen.set(false)"><lucide-icon name="x"></lucide-icon></button></div>
            <div class="modal-b">
              @for (l of byCat('literature'); track l.id) {
                <div class="pick" (click)="litOpen.set(false); attach(l, selId())"><lucide-icon name="book-open"></lucide-icon><span style="flex:1"><b>{{ l.title }}</b><div class="muted small">{{ l.docType.replaceAll('_', ' ') }}</div></span><lucide-icon name="plus"></lucide-icon></div>
              } @empty { <p class="muted">Add write-ups under Library, Bid literature.</p> }
            </div>
          </div>
        </div>
      }

      @if (redraftFor(); as s) {
        <div class="modal-bg open" (click)="redraftFor.set(null)">
          <div class="modal" (click)="$event.stopPropagation()">
            <div class="modal-h"><h3>Redraft “{{ s.title }}”</h3><button class="ib" (click)="redraftFor.set(null)"><lucide-icon name="x"></lucide-icon></button></div>
            <div class="modal-b">
              <div class="field"><label>What should change?</label><textarea class="inp" rows="4" [(ngModel)]="instruction" placeholder="For example: make it more concise, stress our grievance-system experience, align to clause 6.3"></textarea></div>
              <div class="seg" style="max-width:360px">@for (t of tones; track t[0]) { <button [class.on]="tone === t[0]" (click)="tone = t[0]">{{ t[1] }}</button> }</div>
              <p class="small muted">The agent rewrites using the RFP clauses and library documents cited for this section, and keeps your fill-in fields and page references. Names and identifiers are redacted before the request is sent.</p>
            </div>
            <div class="modal-f"><button class="btn" (click)="redraftFor.set(null)">Cancel</button><button class="btn primary" (click)="redraft(s)"><lucide-icon name="sparkles"></lucide-icon>Redraft section</button></div>
          </div>
        </div>
      }
    } @else { <p class="muted" style="padding:24px">Loading…</p> }`,
})
export class BuilderPage implements OnInit {
  private api = inject(Api);
  private pdf = inject(PdfService);
  private router = inject(Router);
  private toast = inject(Toast);
  private current = inject(CurrentBid);
  readonly id = input.required<string>();
  private editor = viewChild<ElementRef<HTMLDivElement>>('editor');

  detail = signal<BidDetail | null>(null);
  sections = signal<Section[]>([]);
  library = signal<LibraryDoc[]>([]);
  map = signal<PageMap | null>(null);
  rawDoc = signal<ReturnType<PdfService['bidPreview']> | null>(null);
  selId = signal<string | null>(null);
  over = signal<string | null>(null);
  libOpen = signal(false);
  picker = signal<Section | null>(null);
  pickTab = signal<Group>('experience');
  chosen = signal(new Set<string>());
  litOpen = signal(false);
  redraftFor = signal<Section | null>(null);
  redrafting = signal<{ title: string } | null>(null);
  instruction = '';
  tone = 'formal';
  tones = [['formal', 'Formal'], ['concise', 'Concise'], ['detailed', 'Detailed']];
  libOrder = LIB_ORDER;
  pickCats: Group[] = ['bid', 'experience', 'statutory', 'hr', 'uploads'];
  icon = ICON;
  fillField = '<mark class="fill">[field]</mark>&nbsp;';
  dirty = new Set<string>();

  selected = computed(() => this.sections().find(s => s.id === this.selId()) ?? null);
  included = computed(() => this.sections().filter(s => s.include).length);
  gaps = computed(() => (this.detail()?.analysis.match?.pq ?? []).filter(r => r.status === 'gap' && r.category !== 'manpower'));
  gapText = computed(() => this.gaps().map(g => `Clause ${g.clause}: ${g.note}`).join(' '));
  fills = computed(() => this.sections().filter(s => s.include && s.kind === 'text').reduce((n, s) => n + (s.html.match(/<mark class="fill">/g)?.length ?? 0), 0));

  private loadedFor: string | null = null;
  private destroyRef = inject(DestroyRef);
  private saveTimer?: ReturnType<typeof setTimeout>;

  constructor() {
    effect(() => {
      const el = this.editor()?.nativeElement, s = this.selected();
      if (el && s && s.kind === 'text' && this.loadedFor !== s.id) {
        el.innerHTML = `<h2 class="sec-title" contenteditable="false">${this.esc(s.title)}</h2>` + (s.html || '<p>Start writing…</p>');
        this.loadedFor = s.id;
      }
      if (!el) this.loadedFor = null;
    });
  }

  ngOnInit(): void {
    this.load(true);
    forkJoin([this.api.bidDocuments(this.id()), this.api.library()]).subscribe(([own, lib]) => this.library.set([...own, ...lib]));
    this.api.pages(this.id()).subscribe({ next: m => { this.map.set(m); this.rawDoc.set(this.pdf.bidPreview(this.id(), m.built_at)); }, error: () => undefined });
  }

  load(selectFirst = false): void {
    this.api.bid(this.id()).subscribe(d => {
      if (!d.sections.length) { this.router.navigate(['/bids', d.bid.id]); return; }
      this.detail.set(d);
      this.current.set(d.bid, true);
      this.sections.set(d.sections);
      if (selectFirst || !d.sections.some(s => s.id === this.selId())) this.selId.set(d.sections.find(s => s.kind === 'text')?.id ?? d.sections[0].id);
    });
  }

  // ---- helpers
  esc(s: string): string { return s.replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]!)); }
  kindIcon(k: string): string { return k === 'text' ? 'file-text' : k === 'docs' ? 'files' : k === 'cover' ? 'book-open' : 'list'; }
  catLabel(c: string): string { return c === 'bid' ? 'This bid' : CATEGORIES.find(x => x.key === c)?.label ?? c; }
  byCat(c: Group): LibraryDoc[] { return this.library().filter(d => c === 'bid' ? !!bidOf(d) : d.category === c && !bidOf(d)); }
  inSection(s: Section, id: string): boolean { return s.documents.some(d => d.id === id); }
  expiring(v: string | null): boolean { return !!v && new Date(v).getTime() - Date.now() < 120 * 86_400_000; }
  docPdf(id: string, status: string) { return status === 'ready' ? this.pdf.libraryDoc(id) : null; }
  viewDoc(id: string): void { this.api.docFile(id, true).subscribe(openBlob); }
  sectionStart(id: string): number | null { return this.map()?.sections.find(x => x.id === id)?.start ?? null; }
  pageRange(id: string): string {
    const m = this.map(), sec = m?.sections.find(x => x.id === id);
    if (!m || !sec) return '';
    const num = (i: number) => (m.numbering.skip_cover && m.pages[0]?.kind === 'cover' && i === 0) ? null : i - (m.numbering.skip_cover && m.pages[0]?.kind === 'cover' ? 1 : 0) + m.numbering.start;
    const a = num(sec.start), b = num(sec.start + sec.count - 1);
    return a === null ? 'cover' : 'p. ' + (a === b ? a : `${a}–${b}`);
  }

  // ---- selection & editing
  select(id: string): void { this.flush(); this.selId.set(id); this.libOpen.set(false); }
  cmd(e: Event, command: string, value?: string): void {
    e.preventDefault();
    document.execCommand(command, false, value);
    const s = this.selected();
    if (s) this.edited(s);
  }
  edited(s: Section): void {
    const el = this.editor()?.nativeElement;
    if (el) { const c = el.cloneNode(true) as HTMLElement; c.querySelector('h2.sec-title')?.remove(); s.html = c.innerHTML; }
    this.dirty.add(s.id);
    clearTimeout(this.saveTimer);
    this.saveTimer = setTimeout(() => this.save(s), 1000);
  }
  flush(): void { const s = this.selected(); if (s && this.dirty.has(s.id)) this.save(s); }
  save(s: Section): void {
    if (!this.dirty.has(s.id)) return;
    this.dirty.delete(s.id);
    clearTimeout(this.saveTimer);
    this.api.updateSection(this.id(), s.id, { title: s.title.trim() || 'Untitled section', html: s.html, include: s.include }).subscribe({
      next: () => this.sections.update(l => [...l]),
      error: e => this.toast.show(errorText(e)),
    });
  }
  toggle(s: Section): void { s.include = !s.include; this.dirty.add(s.id); this.save(s); }
  move(s: Section, dir: number): void {
    const list = [...this.sections()], i = list.findIndex(x => x.id === s.id), j = i + dir;
    if (j < 0 || j >= list.length) return;
    [list[i], list[j]] = [list[j], list[i]];
    this.reorder(list);
  }
  private reorder(list: Section[]): void {
    this.sections.set(list);
    this.api.reorder(this.id(), list.map(x => x.id)).subscribe({ error: e => this.toast.show(errorText(e)) });
  }
  add(kind: 'text' | 'docs'): void {
    this.flush();
    this.api.addSection(this.id(), kind, kind === 'text' ? 'New section' : 'Annexure: new', this.selId() ?? undefined).subscribe(r => { this.selId.set(r.id); this.load(); });
  }
  remove(s: Section): void {
    if (!confirm(`Delete “${s.title}” from the draft?`)) return;
    this.api.deleteSection(this.id(), s.id).subscribe(() => { this.toast.show('Section deleted'); this.selId.set(null); this.load(true); });
  }

  // ---- drag and drop (sections reorder; library documents attach)
  dragSection(e: DragEvent, id: string): void { e.dataTransfer?.setData('text/plain', 'sec:' + id); }
  dragDoc(e: DragEvent, id: string): void { e.dataTransfer?.setData('text/plain', 'doc:' + id); }
  drop(e: DragEvent, target: Section): void {
    e.preventDefault();
    this.over.set(null);
    const v = e.dataTransfer?.getData('text/plain') ?? '';
    if (v.startsWith('sec:')) {
      const from = v.slice(4);
      if (from === target.id) return;
      const list = this.sections().filter(x => x.id !== from);
      list.splice(list.findIndex(x => x.id === target.id), 0, this.sections().find(x => x.id === from)!);
      this.reorder(list);
    } else if (v.startsWith('doc:')) {
      const doc = this.library().find(d => d.id === v.slice(4));
      if (doc) this.attach(doc, target.id);
    }
  }

  async attach(doc: LibraryDoc, sectionId: string | null): Promise<void> {
    const s = this.sections().find(x => x.id === sectionId);
    if (!s) return;
    if (doc.category === 'literature') {
      const { text } = await firstValueFrom(this.api.docText(doc.id));
      const html = `<h3>${this.esc(doc.title)}</h3>` + text.split(/\n\s*\n/).filter(p => p.trim()).slice(0, 40).map(p => `<p>${this.esc(p.trim())}</p>`).join('');
      if (s.kind === 'text') {
        s.html += html; this.loadedFor = null; this.dirty.add(s.id); this.save(s); this.sections.update(l => [...l]);
        this.toast.show(`“${doc.title}” inserted into ${s.title}`);
      } else {
        const r = await firstValueFrom(this.api.addSection(this.id(), 'text', doc.title, s.id));
        await firstValueFrom(this.api.updateSection(this.id(), r.id, { title: doc.title, html, include: true }));
        this.selId.set(r.id); this.load(); this.toast.show('Added as a new section');
      }
      return;
    }
    if (s.kind === 'docs') {
      if (this.inSection(s, doc.id)) { this.toast.show('Already in this annexure'); return; }
      this.saveDocs(s, [...s.documents.map(d => d.id), doc.id], `Added to ${s.title}`);
    } else {
      const r = await firstValueFrom(this.api.addSection(this.id(), 'docs', 'Annexure: ' + doc.title, s.id));
      await firstValueFrom(this.api.setSectionDocs(this.id(), r.id, [doc.id]));
      this.selId.set(r.id); this.load(); this.toast.show('New annexure created');
    }
  }

  // ---- annexure documents
  private saveDocs(s: Section, ids: string[], message?: string): void {
    this.api.setSectionDocs(this.id(), s.id, ids).subscribe({ next: () => { this.load(); if (message) this.toast.show(message); }, error: e => this.toast.show(errorText(e)) });
  }
  moveDoc(s: Section, k: number, dir: number): void {
    const ids = s.documents.map(d => d.id), j = k + dir;
    if (j < 0 || j >= ids.length) return;
    [ids[k], ids[j]] = [ids[j], ids[k]];
    this.saveDocs(s, ids);
  }
  removeDoc(s: Section, id: string): void { this.saveDocs(s, s.documents.map(d => d.id).filter(x => x !== id)); }
  openPicker(s: Section): void { this.chosen.set(new Set()); this.pickTab.set('experience'); this.picker.set(s); }
  choose(id: string): void { this.chosen.update(set => { const n = new Set(set); n.has(id) ? n.delete(id) : n.add(id); return n; }); }
  addChosen(s: Section): void {
    const ids = [...this.chosen()].filter(id => !this.inSection(s, id));
    this.picker.set(null);
    if (ids.length) this.saveDocs(s, [...s.documents.map(d => d.id), ...ids], `${ids.length} document${ids.length > 1 ? 's' : ''} added`);
  }
  uploadFromDevice(s: Section, inp: HTMLInputElement): void {
    const files = [...(inp.files ?? [])];
    inp.value = '';
    if (!files.length) return;
    this.toast.show(`${files.length} file(s) added to this bid's documents and attached`);
    this.api.upload(files, 'uploads', 'internal', { bidId: this.id() }).subscribe({     // into this bid's own documents
      next: ({ created, duplicates }) => {
        // a file already in the library is attached from there instead of being added twice
        const ids = [...created.map(c => c.id), ...duplicates.map(d => d.existing.id)].filter(id => !s.documents.some(d => d.id === id));
        this.library.update(l => [...created, ...l]);
        this.saveDocs(s, [...s.documents.map(d => d.id), ...ids]);
        if (duplicates.length) this.toast.show(`${duplicates.length} file(s) were already in the library and were attached from there`);
      },
      error: e => this.toast.show(errorText(e)),
    });
  }

  // ---- redraft through the privacy gateway
  openRedraft(s: Section): void { this.flush(); this.instruction = ''; this.tone = 'formal'; this.redraftFor.set(s); }
  redraft(s: Section): void {
    this.redraftFor.set(null);
    this.api.redraft(this.id(), s.id, this.instruction, this.tone).subscribe({
      next: ({ jobId }) => {
        this.redrafting.set({ title: s.title });
        let done = false;
        timer(1500, 2500).pipe(switchMap(() => this.api.job(jobId).pipe(catchError(() => EMPTY))), takeWhile(() => !done),
          takeUntilDestroyed(this.destroyRef)).subscribe(j => {
          if (j.status === 'done' || j.status === 'failed') {
            done = true;
            this.redrafting.set(null);
            if (j.status === 'failed') { this.toast.show(j.error ?? 'Redraft failed'); return; }
            // re-render the editor only if it shows the redrafted section, not one being typed in
            if (this.selected()?.id === s.id) this.loadedFor = null;
            this.load();
            this.toast.show(`“${s.title}” redrafted`);
          }
        });
      },
      error: e => this.toast.show(errorText(e)),
    });
  }
}
