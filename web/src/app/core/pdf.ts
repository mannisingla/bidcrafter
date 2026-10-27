import { AfterViewInit, Component, ElementRef, Injectable, OnDestroy, effect, inject, input, signal, viewChild } from '@angular/core';
// legacy build: includes polyfills for browsers without the newest JavaScript features
import { GlobalWorkerOptions, getDocument, type PDFDocumentProxy, type RenderTask } from 'pdfjs-dist/legacy/build/pdf.mjs';
import { firstValueFrom } from 'rxjs';
import { Api } from './api';

GlobalWorkerOptions.workerSrc = new URL('assets/pdfjs/pdf.worker.min.mjs', document.baseURI).href;

/** Opens and caches PDFs (library documents and bid previews) for on-screen rendering. */
const MAX_LIBRARY_DOCS = 20;

@Injectable({ providedIn: 'root' })
export class PdfService {
  private api = inject(Api);
  private cache = new Map<string, Promise<PDFDocumentProxy>>();

  open(blob: Blob): Promise<PDFDocumentProxy> {
    return blob.arrayBuffer().then(data => getDocument({ data }).promise);
  }

  libraryDoc(id: string): Promise<PDFDocumentProxy> {
    const p = this.remember(`doc:${id}`, () => firstValueFrom(this.api.docFile(id, true)).then(b => this.open(b)));
    const docs = [...this.cache.keys()].filter(k => k.startsWith('doc:'));
    while (docs.length > MAX_LIBRARY_DOCS) this.evict(docs.shift()!);      // least recently used first
    return p;
  }

  bidPreview(bidId: string, version: string): Promise<PDFDocumentProxy> {
    const key = `bid:${bidId}:${version}`;
    for (const k of [...this.cache.keys()]) if (k.startsWith(`bid:${bidId}:`) && k !== key) this.evict(k);   // an older build
    return this.remember(key, () => firstValueFrom(this.api.rawPdf(bidId)).then(b => this.open(b)));
  }

  /** Cached, most recently used last; a failed load is not kept, so it can be tried again. */
  private remember(key: string, load: () => Promise<PDFDocumentProxy>): Promise<PDFDocumentProxy> {
    const hit = this.cache.get(key);
    if (hit) { this.cache.delete(key); this.cache.set(key, hit); return hit; }
    const p = load();
    p.catch(() => { if (this.cache.get(key) === p) this.cache.delete(key); });
    this.cache.set(key, p);
    return p;
  }

  private evict(key: string): void {
    const p = this.cache.get(key);
    this.cache.delete(key);
    p?.then(d => d.loadingTask.destroy()).catch(() => undefined);      // frees the document's memory in pdf.js
  }
}

/** One PDF page drawn on a canvas, rendered only when it scrolls into view. */
@Component({
  selector: 'bc-pdf-page',
  template: '<canvas #c></canvas>',
  styles: ':host{display:block;width:100%;height:100%;background:#fff} canvas{display:block;width:100%;height:100%;object-fit:contain}',
})
export class PdfPage implements AfterViewInit, OnDestroy {
  readonly doc = input<Promise<PDFDocumentProxy> | null>(null);
  readonly page = input(1);
  readonly width = input(794);
  private canvas = viewChild.required<ElementRef<HTMLCanvasElement>>('c');
  private host = inject(ElementRef<HTMLElement>);
  private visible = signal(false);
  private observer?: IntersectionObserver;
  private task?: RenderTask;

  constructor() {
    effect(() => {
      const doc = this.doc(), page = this.page(), width = this.width();
      if (this.visible() && doc) void this.draw(doc, page, width);
    });
  }

  ngAfterViewInit(): void {
    // drawn near the viewport; far away again, the canvas gives its memory back (a page at 2x is ~9 MB)
    this.observer = new IntersectionObserver(entries => {
      const near = entries.some(e => e.isIntersecting);
      if (!near && this.visible()) this.release();
      this.visible.set(near);
    }, { rootMargin: '1200px' });
    this.observer.observe(this.host.nativeElement);
  }

  ngOnDestroy(): void { this.observer?.disconnect(); this.release(); }

  private release(): void {
    this.task?.cancel();
    const canvas = this.canvas().nativeElement;
    canvas.width = 0;
    canvas.height = 0;
  }

  private async draw(docPromise: Promise<PDFDocumentProxy>, n: number, width: number): Promise<void> {
    try {
      const doc = await docPromise;
      if (n < 1 || n > doc.numPages) return;
      const page = await doc.getPage(n);
      const base = page.getViewport({ scale: 1 });
      const viewport = page.getViewport({ scale: (width / base.width) * Math.min(window.devicePixelRatio || 1, 2) });
      const canvas = this.canvas().nativeElement;
      this.task?.cancel();
      canvas.width = Math.floor(viewport.width);
      canvas.height = Math.floor(viewport.height);
      this.task = page.render({ canvas, viewport });
      await this.task.promise;
    } catch (e) {
      if ((e as { name?: string })?.name !== 'RenderingCancelledException') console.warn('PDF page render failed', e);
    }
  }
}
