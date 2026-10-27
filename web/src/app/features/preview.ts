import { Component, DestroyRef, ElementRef, OnDestroy, OnInit, computed, effect, inject, input, signal, untracked, viewChild } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { DatePipe, NgTemplateOutlet } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { EMPTY, catchError, firstValueFrom, switchMap, takeWhile, timer } from 'rxjs';
import { Api, errorText } from '../core/api';
import { CurrentBid } from '../core/current-bid';
import { PageDecor, SignBlock } from '../core/decor';
import { Icons } from '../core/icons';
import { BidDetail, BidSettings, Company, PageMap, SignImage } from '../core/models';
import { PdfPage, PdfService } from '../core/pdf';
import { SIGNER_DOWNLOAD, SignerCert, signerCertificates, signerSign } from '../core/signer';
import { Toast } from '../core/toast';

type Sign = NonNullable<BidSettings['sign']>;
type Panel = 'sign' | 'pn' | 'head' | 'bm' | 'pf' | null;
const ZOOMS = [.5, .6, .7, .8, .9, 1, 1.25, 1.5];

/** Preview & sign: the prototype's Acrobat-style viewer over the server-built pages, with live stamping. */
@Component({
  selector: 'bc-preview',
  imports: [DatePipe, FormsModule, Icons, PdfPage, PageDecor, SignBlock, NgTemplateOutlet],
  template: `
    <div class="viewer">
      <div class="vtool">
        <button class="ib" [class.on]="!thumbsOff()" title="Page thumbnails" (click)="thumbsOff.set(!thumbsOff())"><lucide-icon name="panel-left"></lucide-icon></button><span class="sep"></span>
        <button class="ib" title="Previous page" (click)="goto(cur() - 1)"><lucide-icon name="chevron-left"></lucide-icon></button>
        <input class="pgin" [value]="cur() + 1" (change)="goto(+$any($event.target).value - 1)" aria-label="Page"><span class="muted small" style="white-space:nowrap">&nbsp;of {{ count() }}</span>
        <button class="ib" title="Next page" (click)="goto(cur() + 1)"><lucide-icon name="chevron-right"></lucide-icon></button><span class="sep"></span>
        <button class="ib" title="Zoom out" (click)="setZoom(zoom() - .1)"><lucide-icon name="zoom-out"></lucide-icon></button>
        <select (change)="onZoom($any($event.target).value)" aria-label="Zoom">
          @for (z of zooms; track z) { <option [value]="z" [selected]="near(z)">{{ (z * 100).toFixed(0) }}%</option> }
          <option value="fit">Fit width</option>
          @if (customZoom()) { <option selected>{{ (zoom() * 100).toFixed(0) }}%</option> }
        </select>
        <button class="ib" title="Zoom in" (click)="setZoom(zoom() + .1)"><lucide-icon name="zoom-in"></lucide-icon></button><span class="sep"></span>
        <button class="tl" [class.on]="panel() === 'sign'" (click)="togglePanel('sign')"><lucide-icon name="pen-tool"></lucide-icon>Sign &amp; stamp</button>
        <button class="tl" [class.on]="panel() === 'pn'" (click)="togglePanel('pn')"><lucide-icon name="hash"></lucide-icon>Page numbers</button>
        <button class="tl" [class.on]="panel() === 'head'" (click)="togglePanel('head')"><lucide-icon name="heading"></lucide-icon>Header</button>
        <button class="tl" [class.on]="panel() === 'bm'" (click)="togglePanel('bm')"><lucide-icon name="bookmark"></lucide-icon>Bookmarks</button>
        <button class="tl" [class.on]="panel() === 'pf'" (click)="togglePanel('pf')"><lucide-icon name="clipboard-check"></lucide-icon>Pre-submission check</button>
        <span class="grow"></span>
        @if (building()) { <span class="chip blue" style="margin-right:6px"><span class="spin"></span>{{ building() }}</span> }
        <button class="btn sm primary" (click)="openExport()" [disabled]="!map()"><lucide-icon name="printer"></lucide-icon>Export PDF</button>
      </div>
      <div class="vbody">
        @if (!thumbsOff() && map()) {
          <aside class="thumbs show">
            @for (p of map()!.pages; track $index; let i = $index) {
              <div class="thumb" [class.cur]="i === cur()" (click)="goto(i)">
                <div class="tb"><bc-pdf-page [doc]="doc()" [page]="i + 1" [width]="113" />
                  <div style="position:absolute;top:0;left:0;transform:scale(.142);transform-origin:0 0"><ng-container *ngTemplateOutlet="decor; context: { $implicit: i }"></ng-container></div></div>
                {{ num(i) ?? 'Cover' }}
              </div>
            }
          </aside>
        }
        <div class="vcanvas" #canvas (scroll)="onScroll()">
          @if (stale() && map()) {
            <div class="notice warn" style="max-width:794px;width:100%;margin:0"><lucide-icon name="info"></lucide-icon>
              <div>The draft has changed since these pages were built. <button class="btn sm" style="margin-left:6px" (click)="rebuild()">Rebuild pages</button></div></div>
          }
          @if (map(); as m) {
            @for (p of m.pages; track $index; let i = $index) {
              <div class="pwrap" [id]="'pg-' + i" [style.width.px]="794 * zoom()" [style.height.px]="1123 * zoom()">
                <bc-pdf-page [doc]="doc()" [page]="i + 1" [width]="794 * zoom()" />
                <div style="position:absolute;top:0;left:0;transform-origin:0 0" [style.transform]="'scale(' + zoom() + ')'"><ng-container *ngTemplateOutlet="decor; context: { $implicit: i }"></ng-container></div>
              </div>
            }
          } @else if (loaded()) {
            <div class="drop" style="max-width:520px;margin-top:60px;cursor:default">
              <lucide-icon name="file-text"></lucide-icon><b>The pages have not been built yet</b>
              <p style="margin:6px 0 14px">BidCraft assembles the cover, index, drafted sections and every annexure into one A4 document on the server.</p>
              <button class="btn primary" (click)="rebuild()" [disabled]="!!building()"><lucide-icon name="layers"></lucide-icon>Build pages</button>
            </div>
          }
        </div>
        @if (panel()) {
          <aside class="tpanel show">
            <div class="ph"><h3>{{ panelTitle() }}</h3><button class="ib" (click)="panel.set(null)" aria-label="Close panel"><lucide-icon name="x"></lucide-icon></button></div>
            @switch (panel()) {
              @case ('sign') {
                <div class="pb">
                  <div class="stamp-prev"><bc-sign-block [sign]="sign" [image]="sigUrl()" [stampImage]="stampUrl()" /></div>
                  <label class="switch">Signature<input type="checkbox" [(ngModel)]="sign.enabled" (ngModelChange)="changed()"></label>
                  <div class="gallery">
                    @for (img of imagesOf('signature'); track img.id) {
                      <div class="gimg" [class.on]="sign.image_id === img.id">
                        <button class="gpick" (click)="choose('signature', img.id)" [title]="'Use ' + img.name"><img [src]="url(img.id)" alt=""><span>{{ img.name }}</span></button>
                        <button class="gdel" (click)="removeImage(img)" title="Delete this saved signature" aria-label="Delete"><lucide-icon name="x"></lucide-icon></button>
                      </div>
                    }
                    <button class="gimg gadd" (click)="sigFile.click()"><lucide-icon name="upload"></lucide-icon><span>Upload signature</span></button>
                    <input #sigFile type="file" hidden accept="image/png,image/jpeg" (change)="uploadImage('signature', sigFile)">
                  </div>
                  <div class="field"><label>Caption under signature</label><input class="inp" [(ngModel)]="sign.caption" (ngModelChange)="changed()"></div>
                  <label class="switch">Company stamp<input type="checkbox" [(ngModel)]="sign.stamp" (ngModelChange)="changed()"></label>
                  <div class="gallery">
                    @for (img of imagesOf('stamp'); track img.id) {
                      <div class="gimg" [class.on]="sign.stamp_id === img.id">
                        <button class="gpick" (click)="choose('stamp', img.id)" [title]="'Use ' + img.name"><img [src]="url(img.id)" alt=""><span>{{ img.name }}</span></button>
                        <button class="gdel" (click)="removeImage(img)" title="Delete this saved stamp" aria-label="Delete"><lucide-icon name="x"></lucide-icon></button>
                      </div>
                    }
                    <button class="gimg gadd" (click)="stampFile.click()"><lucide-icon name="upload"></lucide-icon><span>Upload stamp</span></button>
                    <input #stampFile type="file" hidden accept="image/png,image/jpeg" (change)="uploadImage('stamp', stampFile)">
                  </div>
                  <p class="small muted" style="margin:-4px 0 12px">Uploaded images are saved for every bid: click one to use it here. A PNG with a transparent background prints cleanly over the page.</p>
                  <h4>Placement</h4>
                  <div class="seg"><button [class.on]="sign.position === 'left'" (click)="sign.position = 'left'; changed()">Bottom left</button><button [class.on]="sign.position === 'right'" (click)="sign.position = 'right'; changed()">Bottom right</button></div>
                  <h4>Apply to</h4>
                  <select class="inp" style="margin-bottom:10px" [(ngModel)]="sign.apply" (ngModelChange)="changed()">
                    <option value="all">All pages</option><option value="not_cover">All pages except cover</option><option value="current">Current page only</option><option value="range">Custom range</option></select>
                  @if (sign.apply === 'range') { <input class="inp" placeholder="e.g. 2-14, 18, 20-25" [(ngModel)]="sign.range" (ngModelChange)="changed()" style="margin-bottom:10px"> }
                  <div class="notice" style="margin:6px 0 0"><lucide-icon name="pen-tool"></lucide-icon><div><b>{{ signedCount() }} of {{ count() }}</b> pages carry the {{ carryText() }}.</div></div>
                  <h4>Digital signature (DSC)</h4>
                  @if (map()?.dsc; as s) {
                    <div class="notice"><lucide-icon name="shield-check"></lucide-icon><div>The PDF is digitally signed by <b>{{ s.signer }}</b> ({{ s.issuer }}) on
                      {{ s.signed_at | date: 'dd MMM yyyy, HH:mm' }}: one signature covering every page, noted on each signed page. Building the PDF again removes it.</div></div>
                  } @else {
                    <p class="small muted" style="margin:0 0 8px">Sign the whole bid with your Class 3 DSC token: one signature covers every page, and each signed page notes it.
                      The token stays on your computer, where BidCraft Signer asks it to sign.</p>
                    @switch (signer()) {
                      @case ('idle') { <button class="btn sm" (click)="findSigner()"><lucide-icon name="shield-check"></lucide-icon>Find BidCraft Signer</button> }
                      @case ('checking') { <p class="small muted">Looking for BidCraft Signer on this computer…</p> }
                      @case ('missing') {
                        <p class="small muted" style="margin:0 0 8px">BidCraft Signer is not running on this computer. On Windows, with the DSC token plugged in, download it, run it and keep its window open.</p>
                        <div class="row wrapx" style="gap:6px"><a class="btn sm" [href]="signerDownload" download><lucide-icon name="download"></lucide-icon>Download BidCraft Signer</a>
                          <button class="btn sm quiet" (click)="findSigner()">Check again</button></div>
                      }
                      @case ('ready') {
                        @if (certs().length) {
                          <select class="inp" (change)="certId.set($any($event.target).value)" aria-label="DSC certificate">
                            @for (c of certs(); track c.thumbprint) { <option [value]="c.thumbprint" [selected]="certId() === c.thumbprint">{{ c.subject }} · {{ c.issuer }} · valid till {{ c.validTo | date: 'dd MMM yyyy' }}</option> }</select>
                          <button class="btn primary" style="margin-top:8px" (click)="signWithDsc()" [disabled]="!!dscBusy() || !!building()"><lucide-icon name="shield-check"></lucide-icon>{{ dscBusy() || 'Digitally sign the bid' }}</button>
                        } @else {
                          <p class="small muted">BidCraft Signer is running, but it found no signing certificate. Plug in the DSC token (its driver must be installed), then
                            <a href="" (click)="$event.preventDefault(); findSigner()">check again</a>.</p>
                        }
                      }
                    }
                  }
                </div>
              }
              @case ('pn') {
                <div class="pb">
                  <label class="switch">Show page numbers<input type="checkbox" [(ngModel)]="pn.enabled" (ngModelChange)="changed()"></label>
                  <div class="field"><label>Format</label><select class="inp" [(ngModel)]="pn.format" (ngModelChange)="changed()">@for (f of formats; track f) { <option [value]="f">{{ f }}</option> }</select></div>
                  <div class="field"><label>Position</label><select class="inp" [(ngModel)]="pn.position" (ngModelChange)="changed()">
                    <option value="center">Bottom centre</option><option value="left">Bottom left</option><option value="right">Bottom right</option><option value="top-right">Top right</option></select></div>
                  <div class="field"><label>Start numbering at</label><input class="inp" type="number" min="1" [(ngModel)]="pn.start" (ngModelChange)="changed(true)"></div>
                  <label class="switch">Leave cover page unnumbered<input type="checkbox" [(ngModel)]="pn.skip_cover" (ngModelChange)="changed(true)"></label>
                  <p class="small muted">The index and every “Pg.” cross-reference in the compliance tables update automatically when numbering changes.</p>
                </div>
              }
              @case ('head') {
                <div class="pb">
                  <label class="switch">Show running header<input type="checkbox" [(ngModel)]="header.enabled" (ngModelChange)="changed()"></label>
                  <div class="field"><label>Left text</label><input class="inp" [(ngModel)]="header.left" (ngModelChange)="changed()" [placeholder]="defaultLeft()"></div>
                  <div class="field"><label>Right text</label><input class="inp" [(ngModel)]="header.right" (ngModelChange)="changed()" [placeholder]="defaultRight()"></div>
                  <p class="small muted">The header is left off the cover page and the enclosed documents: an original document gets only the page number, signature and stamp.</p>
                </div>
              }
              @case ('bm') {
                <div class="pb" style="padding:8px">
                  @for (s of map()?.sections ?? []; track s.id) { <div class="bm" (click)="goto(s.start)"><span>{{ s.title }}</span><span>{{ range(s.start, s.count) }}</span></div> }
                </div>
              }
              @case ('pf') {
                <div class="pb" style="padding-top:4px">
                  @for (c of preflight(); track $index) { <div [class]="'pf ' + c.lvl"><lucide-icon [name]="c.lvl === 'ok' ? 'check' : c.lvl === 'warn' ? 'clock' : 'flag'"></lucide-icon><div>{{ c.text }}@if (c.link) { <a href="#" (click)="$event.preventDefault(); router.navigate(c.link)"> {{ c.linkText }}</a>.}</div></div> }
                </div>
              }
            }
          </aside>
        }
      </div>
    </div>

    <ng-template #decor let-i>
      <bc-page-decor [kind]="map()!.pages[i].kind" [label]="label(i)" [numberPos]="pn.position"
        [headerOn]="header.enabled" [headerLeft]="header.left || defaultLeft()" [headerRight]="header.right || defaultRight()"
        [signed]="signApplies(i)" [sign]="sign" [image]="sigUrl()" [stampImage]="stampUrl()" />
    </ng-template>

    @if (exporting()) {
      <div class="modal-bg open" (click)="closeExport()">
        <div class="modal" (click)="$event.stopPropagation()">
          <div class="modal-h"><h3>Export as a single PDF</h3><button class="ib" (click)="closeExport()"><lucide-icon name="x"></lucide-icon></button></div>
          <div class="modal-b">
            <div class="row wrapx" style="gap:24px;margin-bottom:14px">
              <div><div class="muted small">File name</div><b>{{ fileName() }}</b></div><div><div class="muted small">Pages</div><b>{{ count() }}</b></div>
              <div><div class="muted small">Signed and stamped</div><b>{{ signedCount() }}</b></div><div><div class="muted small">Paper</div><b>A4 portrait</b></div>
            </div>
            @if (issues()) { <div class="notice warn"><lucide-icon name="flag"></lucide-icon><div>{{ issues() }} item{{ issues() > 1 ? 's' : '' }} to review before submission. You can still export a working copy.</div></div> }
            @for (c of preflight(); track $index) { <div [class]="'pf ' + c.lvl"><lucide-icon [name]="c.lvl === 'ok' ? 'check' : c.lvl === 'warn' ? 'clock' : 'flag'"></lucide-icon><div>{{ c.text }}</div></div> }
            @if (map()?.dsc; as s) { <div class="notice"><lucide-icon name="shield-check"></lucide-icon><div>Digitally signed by <b>{{ s.signer }}</b> on {{ s.signed_at | date: 'dd MMM yyyy, HH:mm' }}: the download is the signed PDF.</div></div> }
            <p class="small muted" style="margin-top:14px">The PDF is generated on your server with page numbers, signature and stamp on every page, the header on the bid’s own pages (enclosed documents stay exactly as uploaded) and bookmarks for each section. To sign it with your DSC token, use Sign &amp; stamp → Digital signature.</p>
            @if (exportError()) { <div class="notice warn"><lucide-icon name="flag"></lucide-icon><div>{{ exportError() }}</div></div> }
          </div>
          <div class="modal-f">
            <button class="btn" (click)="closeExport()">Close</button>
            <button class="btn" (click)="exportPdf(false)" [disabled]="!!building()"><lucide-icon name="printer"></lucide-icon>Open for printing</button>
            <button class="btn primary" (click)="exportPdf(true)" [disabled]="!!building()"><lucide-icon name="printer"></lucide-icon>{{ building() ? building() : 'Download PDF' }}</button>
          </div>
        </div>
      </div>
    }`,
})
export class PreviewPage implements OnInit, OnDestroy {
  private api = inject(Api);
  private pdf = inject(PdfService);
  private current = inject(CurrentBid);
  private toast = inject(Toast);
  protected router = inject(Router);
  readonly id = input.required<string>();
  readonly exportParam = input<string | undefined>(undefined, { alias: 'export' });
  private canvasEl = viewChild<ElementRef<HTMLDivElement>>('canvas');

  detail = signal<BidDetail | null>(null);
  map = signal<PageMap | null>(null);
  doc = signal<ReturnType<PdfService['bidPreview']> | null>(null);
  company = signal<Company | null>(null);
  /** saved signature and stamp images, and their object URLs for showing them */
  images = signal<SignImage[]>([]);
  urls = signal<Record<string, string>>({});
  private legacyUrl = signal<string | null>(null);      // a signature uploaded for the bid by an earlier version
  loaded = signal(false);
  zoom = signal(.8);
  cur = signal(0);
  panel = signal<Panel>('sign');
  thumbsOff = signal(window.innerWidth <= 960);
  building = signal('');
  exporting = signal(false);
  exportError = signal('');
  zooms = ZOOMS;
  formats = ['Page {n} of {N}', '{n}', '- {n} -', 'Page {n}', '{n} / {N}'];

  sign: Sign = { enabled: true, image_id: null, caption: 'Authorised signatory', stamp: true, stamp_id: null, position: 'right', apply: 'not_cover', range: '' };
  pn = { enabled: true, format: 'Page {n} of {N}', position: 'center', start: 1, skip_cover: true };
  header = { enabled: true, left: '', right: '' };

  count = computed(() => this.map()?.pages.length ?? 0);
  customZoom = computed(() => !ZOOMS.some(z => Math.abs(z - this.zoom()) < .01));
  stale = computed(() => {
    const m = this.map(), d = this.detail();
    if (!m || !d) return false;
    const order = d.sections.filter(s => s.include).map(s => s.id).join(), built = m.sections.map(s => s.id).join();
    return order !== built || d.sections.some(s => s.updatedAt && s.updatedAt > m.built_at);
  });
  panelTitle = computed(() => ({ sign: 'Sign & stamp', pn: 'Page numbers', head: 'Header', bm: 'Bookmarks', pf: 'Pre-submission check' } as Record<string, string>)[this.panel() ?? ''] ?? '');
  fileName = computed(() => `Technical-bid-${(this.detail()?.bid.rfpRef ?? 'draft').replace(/\//g, '-')}.pdf`);

  private saveTimer?: ReturnType<typeof setTimeout>;
  private numberingTimer?: ReturnType<typeof setTimeout>;
  private destroyRef = inject(DestroyRef);
  /** Settings are saved only once loaded from the server and changed here (never the defaults over real ones). */
  private settingsLoaded = false;
  private settingsDirty = false;
  private exportHandled: string | undefined;

  constructor() {
    // "Export PDF" in the top bar navigates here with a fresh ?export= value, also when this page is already open
    effect(() => {
      const e = this.exportParam(), d = this.detail();
      if (e && d && e !== this.exportHandled) untracked(() => { this.exportHandled = e; this.openExport(); });
    });
  }

  ngOnInit(): void {
    this.api.company().subscribe(c => this.company.set(c));
    this.api.bid(this.id()).subscribe(d => {
      this.detail.set(d);
      this.current.set(d.bid, d.sections.length > 0);
      const s = d.settings;
      this.sign = { ...this.sign, ...(s.sign ?? {}) } as Sign;
      this.pn = { ...this.pn, ...(s.page_numbers ?? {}) };
      this.header = { ...this.header, ...(s.header ?? {}) };
      this.settingsLoaded = true;
      if (this.sign.image_path) this.api.signatureImage(d.bid.id).subscribe({ next: b => this.legacyUrl.set(URL.createObjectURL(b)), error: () => undefined });
      this.loadImages();
      let known = false;
      try { known = localStorage.getItem('bc-signer') === '1'; } catch { /* storage blocked */ }
      if (known) this.findSigner();          // used on this computer before: look for it straight away
      const building = d.jobs.find(j => j.type === 'assemble_pdf' && (j.status === 'queued' || j.status === 'running'));
      if (building) this.watch('Building the PDF…', building.id);
      this.loadPages();
    });
  }

  ngOnDestroy(): void {
    this.flushSave();
    for (const url of [...Object.values(this.urls()), this.legacyUrl()]) if (url) URL.revokeObjectURL(url);
  }

  private loadPages(): void {
    const d = this.detail();
    if (!d?.bid.hasPdf) { this.loaded.set(true); return; }
    this.api.pages(d.bid.id).subscribe({
      next: m => { this.map.set(m); this.doc.set(this.pdf.bidPreview(d.bid.id, m.built_at)); this.loaded.set(true); },
      error: () => this.loaded.set(true),
    });
  }

  // ---- numbering, stamping rules (same as the server)
  num(i: number): number | null {
    const m = this.map();
    const skip = this.pn.skip_cover && m?.pages[0]?.kind === 'cover';
    if (skip && i === 0) return null;
    return i - (skip ? 1 : 0) + (+this.pn.start || 1);
  }
  label(i: number): string | null {
    const n = this.num(i);
    if (!this.pn.enabled || n === null) return null;
    return this.pn.format.replace('{n}', String(n)).replace('{N}', String(this.num(this.count() - 1) ?? this.count()));
  }
  range(start: number, count: number): string {
    const a = this.num(start), b = this.num(start + count - 1);
    return a === null ? '—' : a === b ? String(a) : `${a}–${b}`;
  }
  private rangeSet(): Set<number> {
    const out = new Set<number>();
    for (const part of (this.sign.range || '').split(',')) {
      const m = part.trim().match(/^(\d+)(?:\s*[-–]\s*(\d+))?$/);
      if (m) { const a = +m[1], b = +(m[2] || m[1]); for (let x = Math.min(a, b); x <= Math.min(Math.max(a, b), this.count()); x++) out.add(x); }
    }
    return out;
  }
  signApplies(i: number): boolean {
    if (!this.sign.enabled && !this.sign.stamp) return false;
    const kind = this.map()?.pages[i]?.kind;
    switch (this.sign.apply) {
      case 'all': return true;
      case 'current': return i === this.cur();
      case 'range': return this.rangeSet().has(i + 1);
      default: return kind !== 'cover';
    }
  }
  signedCount(): number { let n = 0; for (let i = 0; i < this.count(); i++) if (this.signApplies(i)) n++; return n; }
  carryText(): string { return [this.sign.enabled ? 'signature' : '', this.sign.stamp && this.stampUrl() ? 'stamp' : ''].filter(Boolean).join(' and ') || 'nothing'; }
  defaultLeft(): string { const d = this.detail(); return d?.bid.rfpRef || d?.bid.title || ''; }
  defaultRight(): string { return `Technical bid – ${this.company()?.name ?? ''}`; }

  // ---- viewer
  near(z: number): boolean { return Math.abs(z - this.zoom()) < .01; }
  onZoom(v: string): void { v === 'fit' ? this.setZoom(((this.canvasEl()?.nativeElement.clientWidth ?? 834) - 40) / 794) : this.setZoom(+v); }
  setZoom(z: number): void { const c = this.cur(); this.zoom.set(Math.max(.3, Math.min(2, Math.round(z * 100) / 100))); setTimeout(() => this.goto(c, false)); }
  togglePanel(p: Exclude<Panel, null>): void { this.panel.set(this.panel() === p && window.innerWidth <= 960 ? null : p); }
  goto(i: number, smooth = true): void {
    i = Math.max(0, Math.min(this.count() - 1, i || 0));
    this.cur.set(i);
    const el = document.getElementById('pg-' + i);
    if (el) this.canvasEl()?.nativeElement.scrollTo({ top: el.offsetTop - 20, behavior: smooth ? 'smooth' : 'auto' });
  }
  onScroll(): void {
    const vc = this.canvasEl()?.nativeElement;
    if (!vc) return;
    const mid = vc.scrollTop + vc.clientHeight / 3;
    let c = 0;
    vc.querySelectorAll<HTMLElement>('.pwrap').forEach((el, i) => { if (el.offsetTop <= mid) c = i; });
    if (c !== this.cur()) this.cur.set(c);
  }

  // ---- settings: live on screen, saved in the background
  changed(numbering = false): void {
    this.settingsDirty = true;
    this.changedSinceBuild = true;
    this.sign = { ...this.sign };            // a new object, so the stamp and signature drawn on the pages follow
    clearTimeout(this.saveTimer);
    this.saveTimer = setTimeout(() => this.flushSave(), 800);
    if (numbering && this.map()) { clearTimeout(this.numberingTimer); this.numberingTimer = setTimeout(() => this.rebuild('Updating index and page references…'), 1500); }
  }
  private settings(): BidSettings {
    const sign = { ...this.sign, range: this.sign.apply === 'current' ? String(this.cur() + 1) : this.sign.range };
    return { header: this.header, page_numbers: { ...this.pn, start: +this.pn.start || 1 }, sign };
  }
  private flushSave(): Promise<unknown> {
    clearTimeout(this.saveTimer);
    if (!this.settingsLoaded || !this.settingsDirty) return Promise.resolve();
    this.settingsDirty = false;
    return firstValueFrom(this.api.saveSettings(this.id(), this.settings()))
      .catch(e => { this.settingsDirty = true; this.toast.show(errorText(e)); });
  }
  // ---- saved signature and stamp images
  imagesOf(kind: SignImage['kind']): SignImage[] { return this.images().filter(x => x.kind === kind); }
  url(id: string): string { return this.urls()[id] ?? ''; }
  sigUrl(): string | null { return (this.sign.image_id && this.urls()[this.sign.image_id]) || (this.sign.image_id ? null : this.legacyUrl()); }
  stampUrl(): string | null { return (this.sign.stamp_id && this.urls()[this.sign.stamp_id]) || null; }
  private loadImages(): void {
    this.api.signImages().subscribe(list => { this.images.set(list); list.forEach(x => this.loadUrl(x.id)); });
  }
  private loadUrl(id: string): void {
    if (this.urls()[id]) return;
    this.api.signImageFile(id).subscribe({ next: b => this.urls.update(u => ({ ...u, [id]: URL.createObjectURL(b) })), error: () => undefined });
  }
  choose(kind: SignImage['kind'], id: string): void {
    if (kind === 'signature') { this.sign.image_id = id; this.sign.enabled = true; } else { this.sign.stamp_id = id; this.sign.stamp = true; }
    this.changed();
  }
  uploadImage(kind: SignImage['kind'], input: HTMLInputElement): void {
    const file = input.files?.[0];
    input.value = '';
    if (!file) return;
    this.api.uploadSignImage(file, kind).subscribe({
      next: img => { this.images.update(l => [img, ...l]); this.loadUrl(img.id); this.choose(kind, img.id); this.toast.show(`${kind === 'signature' ? 'Signature' : 'Stamp'} saved and applied`); },
      error: e => this.toast.show(errorText(e)),
    });
  }
  removeImage(img: SignImage): void {
    if (!confirm(`Delete the saved ${img.kind} “${img.name}”? Bids that use it are built without it until another is chosen.`)) return;
    this.api.deleteSignImage(img.id).subscribe({
      next: () => {
        this.images.update(l => l.filter(x => x.id !== img.id));
        if (this.sign.image_id === img.id) { this.sign.image_id = null; this.changed(); }
        if (this.sign.stamp_id === img.id) { this.sign.stamp_id = null; this.changed(); }
      },
      error: e => this.toast.show(errorText(e)),
    });
  }

  // ---- building on the server
  async rebuild(label = 'Building pages…'): Promise<void> {
    if (!this.mayRemoveSignature()) return;
    this.building.set(label);
    await this.flushSave();
    this.api.assemble(this.id()).subscribe({ next: r => this.watch(label, r.jobId), error: e => { this.building.set(''); this.toast.show(errorText(e)); } });
  }
  /** Follows the build job itself: the bid keeps its old status until the worker picks the job up. */
  private watch(label: string, jobId: number, after?: () => void, failed?: () => void): void {
    this.building.set(label);
    let done = false;
    timer(2000, 2500).pipe(
      switchMap(() => this.api.job(jobId).pipe(catchError(() => EMPTY))),
      takeWhile(() => !done),
      takeUntilDestroyed(this.destroyRef),
    ).subscribe(j => {
      if (j.status !== 'done' && j.status !== 'failed') return;
      done = true;
      this.building.set('');
      if (j.status === 'done') this.changedSinceBuild = false;
      this.api.bid(this.id()).subscribe(d => this.detail.set(d));
      if (j.status === 'failed') {
        const msg = j.error ?? 'Building the PDF failed';
        this.exportError.set(msg); this.toast.show(msg); failed?.();
        return;
      }
      this.loadPages();
      after?.();
    });
  }

  // ---- export & pre-submission check
  openExport(): void { this.exportError.set(''); this.exporting.set(true); }
  closeExport(): void { this.exporting.set(false); }
  async exportPdf(download: boolean): Promise<void> {
    this.exportError.set('');
    // opened now, during the click: a window opened after the build would be stopped by the popup blocker
    const signedAsIs = !!this.map()?.dsc && !this.stale() && !this.changedSinceBuild;
    if (!signedAsIs && !this.mayRemoveSignature()) return;
    const win = download ? null : window.open('', '_blank');
    const deliver = () => this.api.pdf(this.id()).subscribe(blob => {
      const url = URL.createObjectURL(blob);
      if (download) { const a = document.createElement('a'); a.href = url; a.download = this.fileName(); a.click(); this.toast.show('PDF downloaded'); }
      else if (win) win.location.href = url;
      else window.open(url, '_blank', 'noopener');
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    });
    if (signedAsIs) { deliver(); return; }        // the digitally signed PDF, not a new build without the signature
    this.building.set('Generating PDF…');
    await this.flushSave();
    this.api.assemble(this.id()).subscribe({
      next: r => this.watch('Generating PDF…', r.jobId, deliver, () => win?.close()),
      error: e => { win?.close(); this.building.set(''); this.exportError.set(errorText(e)); },
    });
  }

  /** A new build replaces the digitally signed PDF: ask first. */
  private mayRemoveSignature(): boolean {
    return !this.map()?.dsc || confirm('The PDF is digitally signed with a DSC. Building it again removes that signature; sign it again afterwards. Continue?');
  }

  // ---- digital signature with a DSC token (BidCraft Signer on this computer; the server side is ai-service pdf/dsc.py)
  signerDownload = SIGNER_DOWNLOAD;
  signer = signal<'idle' | 'checking' | 'missing' | 'ready'>('idle');
  certs = signal<SignerCert[]>([]);
  certId = signal('');
  dscBusy = signal('');
  private changedSinceBuild = false;
  async findSigner(): Promise<void> {
    this.signer.set('checking');
    try {
      const list = await signerCertificates();
      this.certs.set(list);
      if (!list.some(c => c.thumbprint === this.certId())) this.certId.set(list[0]?.thumbprint ?? '');
      this.signer.set('ready');
      try { localStorage.setItem('bc-signer', '1'); } catch { /* storage blocked: ask again next time */ }
    } catch {
      this.signer.set('missing');
    }
  }
  async signWithDsc(): Promise<void> {
    const cert = this.certs().find(c => c.thumbprint === this.certId());
    if (!cert) return;
    this.exportError.set('');
    try {
      if (!this.detail()?.bid.hasPdf || this.stale() || this.changedSinceBuild) {     // sign what is on screen
        this.dscBusy.set('Building the PDF…');
        await this.buildNow();
      }
      this.dscBusy.set('Preparing…');
      const prep = await firstValueFrom(this.api.dscPrepare(this.id(), { certificate: cert.certificate, chain: cert.chain }));
      this.dscBusy.set('Confirm on this computer, then enter the token PIN…');
      const signature = await signerSign(cert.thumbprint, prep.hash, this.fileName());
      this.dscBusy.set('Finishing…');
      const done = await firstValueFrom(this.api.dscFinish(this.id(), { session: prep.session, signature }));
      this.toast.show(`Digitally signed by ${done.signer}`);
      this.loadPages();
    } catch (e) {
      const msg = errorText(e);
      this.exportError.set(msg);
      this.toast.show(msg);
    } finally {
      this.dscBusy.set('');
    }
  }
  /** Builds the PDF and waits for it (the DSC signs the built file). */
  private async buildNow(): Promise<void> {
    await this.flushSave();
    const { jobId } = await firstValueFrom(this.api.assemble(this.id()));
    for (let i = 0; i < 240; i++) {
      await new Promise(r => setTimeout(r, 2500));
      const j = await firstValueFrom(this.api.job(jobId));
      if (j.status === 'failed') throw new Error(j.error ?? 'Building the PDF failed');
      if (j.status === 'done') {
        this.changedSinceBuild = false;
        this.detail.set(await firstValueFrom(this.api.bid(this.id())));
        const m = await firstValueFrom(this.api.pages(this.id()));
        this.map.set(m);
        this.doc.set(this.pdf.bidPreview(this.id(), m.built_at));
        return;
      }
    }
    throw new Error('Building the PDF is taking too long; try again in a minute');
  }
  preflight(): { lvl: 'ok' | 'warn' | 'bad'; text: string; link?: string[]; linkText?: string }[] {
    const d = this.detail();
    if (!d) return [];
    const out: { lvl: 'ok' | 'warn' | 'bad'; text: string; link?: string[]; linkText?: string }[] = [];
    const pq = d.analysis.match?.pq ?? [];
    const gaps = pq.filter(r => r.status === 'gap' && r.category !== 'manpower');
    out.push(gaps.length ? { lvl: 'bad', text: `${gaps.length} pre-qualification criteri${gaps.length > 1 ? 'a have' : 'on has'} no supporting document (${gaps.map(g => g.clause).join(', ')}).`, link: ['/bids', d.bid.id], linkText: 'See the analysis' }
      : { lvl: 'ok', text: `All ${pq.length - pq.filter(r => r.status === 'gap').length} other pre-qualification criteria have supporting documents.` });
    for (const r of pq.filter(x => x.status === 'gap' && x.category === 'manpower')) {
      out.push({ lvl: 'warn', text: `Clause ${r.clause}: a certificate of IT manpower strength has been drafted; fill in the headcount from the HR register.`, link: ['/bids', d.bid.id, 'builder'], linkText: 'Open the builder' });
    }
    const fills = d.sections.filter(s => s.include && s.kind === 'text').reduce((n, s) => n + (s.html.match(/<mark class="fill">/g)?.length ?? 0), 0);
    out.push(fills ? { lvl: 'bad', text: `${fills} highlighted fields are still unfilled in the draft.`, link: ['/bids', d.bid.id, 'builder'], linkText: 'Open the builder' } : { lvl: 'ok', text: 'No unfilled fields.' });
    const seen = new Set<string>();
    for (const s of d.sections.filter(x => x.include && x.kind === 'docs')) for (const doc of s.documents) {
      if (seen.has(doc.id) || !doc.validUntil) continue;
      seen.add(doc.id);
      if (new Date(doc.validUntil).getTime() - Date.now() < 120 * 86_400_000) out.push({ lvl: 'warn', text: `${doc.title} expires on ${doc.validUntil}, within the contract period. Consider adding the renewal plan.` });
    }
    const n = this.signedCount(), total = this.count();
    out.push(n >= total - 1 ? { lvl: 'ok', text: `Signature and stamp on ${n} of ${total} pages.` } : { lvl: 'warn', text: `Only ${n} of ${total} pages are signed. Most RFPs require every page signed and stamped.` });
    out.push(this.pn.enabled ? { lvl: 'ok', text: 'Pages are numbered and the index matches.' } : { lvl: 'warn', text: 'Page numbers are off. The index will show page numbers that are not printed.' });
    out.push({ lvl: 'ok', text: 'Financial bid is excluded from this document, as required for envelope B.' });
    return out;
  }
  issues(): number { return this.preflight().filter(c => c.lvl !== 'ok').length; }
}
