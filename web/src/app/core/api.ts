import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  Analysis, BidDetail, BidFolder, BidSettings, BidSummary, Category, Company, DscInfo, EligibilityDecision, JobInfo, LibraryDoc, PageMap,
  SignImage, UploadResult, User,
} from './models';

@Injectable({ providedIn: 'root' })
export class Api {
  private http = inject(HttpClient);

  login(email: string, password: string) {
    return this.http.post<{ token: string; user: User }>('/api/auth/login', { email, password });
  }

  // library
  library(category?: Category) {
    return this.http.get<LibraryDoc[]>('/api/library', { params: category ? { category } : {} });
  }
  /** The documents uploaded for one bid (EMD, power of attorney…), kept out of the library. */
  bidDocuments(bidId: string) { return this.http.get<LibraryDoc[]>('/api/library', { params: { bid: bidId } }); }
  upload(files: File[], category: Category, sensitivity = 'internal', opts: { docType?: string; parentId?: string; title?: string; bidId?: string } = {}) {
    const form = new FormData();
    files.forEach(f => form.append('files', f, f.name));
    form.append('category', category);
    form.append('sensitivity', sensitivity);
    if (opts.docType) form.append('docType', opts.docType);
    if (opts.parentId) form.append('parentId', opts.parentId);
    if (opts.title) form.append('title', opts.title);
    if (opts.bidId) form.append('bidId', opts.bidId);
    return this.http.post<UploadResult>('/api/library', form);
  }
  /** Read a project's details from its PO, certificate or RFP, or a candidate's from their CV, again. */
  extractProject(docId: string) { return this.http.post(`/api/library/${docId}/extract`, {}); }
  updateDoc(id: string, body: Pick<LibraryDoc, 'title' | 'category' | 'docType' | 'sensitivity' | 'validUntil' | 'metadata'>) {
    return this.http.put<LibraryDoc>(`/api/library/${id}`, body);
  }
  reprocess(id: string) { return this.http.post(`/api/library/${id}/reprocess`, {}); }
  deleteDoc(id: string) { return this.http.delete(`/api/library/${id}`); }
  docFile(id: string, pdf = true): Observable<Blob> {
    return this.http.get(`/api/library/${id}/file`, { params: { pdf }, responseType: 'blob' });
  }
  search(q: string, category?: string) {
    return this.http.get<{ results: { documentId: string; title: string; category: string; snippet: string; similarity: number }[] }>(
      '/api/library/search', { params: category ? { q, category } : { q } });
  }

  // bids
  bids() { return this.http.get<BidSummary[]>('/api/bids'); }
  /** A new tender from its documents; kinds[i] is the kind of files[i] (rfp, corrigendum, addendum…). */
  createBid(files: File[], kinds: string[], title: string) {
    const form = new FormData();
    files.forEach((f, i) => { form.append('files', f, f.name); form.append('kinds', kinds[i] ?? 'rfp'); });
    if (title) form.append('title', title);
    return this.http.post<{ bid: BidSummary; jobId: number }>('/api/bids', form);
  }
  bid(id: string) { return this.http.get<BidDetail>(`/api/bids/${id}`); }
  addBidDocuments(id: string, files: File[], kinds: string[]) {
    const form = new FormData();
    files.forEach((f, i) => { form.append('files', f, f.name); form.append('kinds', kinds[i] ?? 'rfp'); });
    return this.http.post<{ jobId: number }>(`/api/bids/${id}/documents`, form);
  }
  removeBidDocument(id: string, path: string) { return this.http.delete<{ jobId: number }>(`/api/bids/${id}/documents`, { params: { path } }); }
  bidDocumentFile(id: string, path: string): Observable<Blob> {
    return this.http.get(`/api/bids/${id}/documents/file`, { params: { path }, responseType: 'blob' });
  }
  bidFolder(id: string) { return this.http.get<BidFolder>(`/api/bids/${id}/folder`); }
  bidFolderFile(id: string, name: string): Observable<Blob> {
    return this.http.get(`/api/bids/${id}/folder/file`, { params: { name }, responseType: 'blob' });
  }
  bidFolderZip(id: string): Observable<Blob> { return this.http.get(`/api/bids/${id}/folder/zip`, { responseType: 'blob' }); }
  reanalyse(id: string) { return this.http.post<{ jobId: number }>(`/api/bids/${id}/analyze`, {}); }
  checkEligibility(id: string) { return this.http.post<{ jobId: number }>(`/api/bids/${id}/eligibility`, {}); }
  decideEligibility(id: string, kind: 'pq' | 'personnel', index: number, decision: EligibilityDecision) {
    return this.http.put<{ analysis: Analysis; draftUpdating: boolean }>(`/api/bids/${id}/eligibility/${kind}/${index}`, decision);
  }
  updateRfp(id: string, values: Record<string, string | null>) {
    return this.http.put<{ analysis: Analysis; bid: BidSummary }>(`/api/bids/${id}/rfp`, values);
  }
  draft(id: string, overwrite = false) { return this.http.post<{ jobId: number }>(`/api/bids/${id}/draft`, { overwrite }); }
  assemble(id: string) { return this.http.post<{ jobId: number }>(`/api/bids/${id}/assemble`, {}); }
  pdf(id: string): Observable<Blob> { return this.http.get(`/api/bids/${id}/pdf`, { responseType: 'blob' }); }
  saveSettings(id: string, settings: BidSettings) { return this.http.put<BidSettings>(`/api/bids/${id}/settings`, settings); }
  // saved signature and stamp images
  signImages() { return this.http.get<SignImage[]>('/api/sign-images'); }
  uploadSignImage(file: File, kind: SignImage['kind']) {
    const form = new FormData();
    form.append('file', file, file.name);
    form.append('kind', kind);
    return this.http.post<SignImage>('/api/sign-images', form);
  }
  signImageFile(id: string): Observable<Blob> { return this.http.get(`/api/sign-images/${id}/file`, { responseType: 'blob' }); }
  deleteSignImage(id: string) { return this.http.delete(`/api/sign-images/${id}`); }
  updateSection(bidId: string, sectionId: string, body: { title: string; html: string; include: boolean }) {
    return this.http.put(`/api/bids/${bidId}/sections/${sectionId}`, body);
  }
  addSection(bidId: string, kind: 'text' | 'docs', title: string, afterId?: string) {
    return this.http.post<{ id: string }>(`/api/bids/${bidId}/sections`, { kind, title, afterId: afterId ?? null });
  }
  deleteSection(bidId: string, sectionId: string) { return this.http.delete(`/api/bids/${bidId}/sections/${sectionId}`); }
  reorder(bidId: string, ids: string[]) { return this.http.post(`/api/bids/${bidId}/sections/reorder`, { ids }); }
  setSectionDocs(bidId: string, sectionId: string, documentIds: string[]) {
    return this.http.put(`/api/bids/${bidId}/sections/${sectionId}/documents`, { documentIds });
  }

  rawPdf(id: string): Observable<Blob> { return this.http.get(`/api/bids/${id}/pdf`, { params: { raw: true }, responseType: 'blob' }); }
  pages(id: string) { return this.http.get<PageMap>(`/api/bids/${id}/pages`); }
  /** Digital signature with a DSC token: the hash for BidCraft Signer to sign, then the token's signature. */
  dscPrepare(id: string, body: { certificate: string; chain: string[] }) {
    return this.http.post<{ session: string; hash: string; algorithm: string; signer: string }>(`/api/bids/${id}/dsc/prepare`, body);
  }
  dscFinish(id: string, body: { session: string; signature: string }) { return this.http.post<DscInfo>(`/api/bids/${id}/dsc/finish`, body); }
  signatureImage(id: string): Observable<Blob> { return this.http.get(`/api/bids/${id}/signature`, { responseType: 'blob' }); }
  redraft(bidId: string, sectionId: string, instruction: string, tone: string) {
    return this.http.post<{ jobId: number }>(`/api/bids/${bidId}/sections/${sectionId}/redraft`, { instruction, tone });
  }
  docText(id: string) { return this.http.get<{ title: string; text: string }>(`/api/library/${id}/text`); }
  company() { return this.http.get<Company>('/api/company'); }

  // jobs, audit
  job(id: number) { return this.http.get<JobInfo>(`/api/jobs/${id}`); }
  audit() {
    return this.http.get<{ id: number; at: string; actor: string; action: string; entity: string | null; entityId: string | null; detail: unknown }[]>('/api/audit');
  }
}

export function errorText(err: unknown): string {
  if (err instanceof HttpErrorResponse) {
    return (err.error && typeof err.error === 'object' && 'message' in err.error) ? String(err.error.message)
      : err.status === 0 ? 'The server could not be reached' : `Request failed (${err.status})`;
  }
  if (err instanceof Error && err.message) return err.message;
  return 'Something went wrong';
}

export function openBlob(blob: Blob): void {
  const url = URL.createObjectURL(blob);
  window.open(url, '_blank', 'noopener');
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}
