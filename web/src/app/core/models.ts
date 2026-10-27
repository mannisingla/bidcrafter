export type Category = 'experience' | 'statutory' | 'hr' | 'literature' | 'uploads';
export type Sensitivity = 'public' | 'internal' | 'confidential' | 'restricted';

export interface LibraryDoc {
  id: string;
  category: Category;
  docType: string;
  title: string;
  originalName: string;
  mimeType: string;
  pageCount: number | null;
  status: 'uploaded' | 'processing' | 'ready' | 'failed';
  error: string | null;
  metadata: Record<string, unknown>;
  validUntil: string | null;
  sensitivity: Sensitivity;
  createdAt: string;
  updatedAt: string;
  fileSize: number | null;
}

/** Files identical to one already in the library are not added again; the copy on record is returned instead. */
export interface UploadResult {
  created: LibraryDoc[];
  duplicates: { fileName: string; existing: LibraryDoc }[];
}

/** "Already in the library" note for an upload's duplicates, or ''. */
export function duplicateNote(r: UploadResult): string {
  return r.duplicates.length
    ? 'Already in the library, not added again: ' + r.duplicates.map(d => `${d.fileName} (as “${d.existing.title}”)`).join('; ') + '.'
    : '';
}

/** PO numbers compared without spaces, punctuation or case: "NICSI/11/16600" = "nicsi 11-16600". */
export function poKey(value: unknown): string {
  return String(value ?? '').toUpperCase().replace(/[^A-Z0-9]/g, '');
}

/** Where a document not yet ready is: waiting its turn, or how many of its scanned pages are OCR'd. */
export function readingLabel(d: LibraryDoc): string {
  if (d.status === 'uploaded') return 'Waiting to be read';
  const r = d.metadata['reading'] as { done?: number; total?: number } | undefined;
  return r?.total ? `Reading scanned pages: ${r.done ?? 0} of ${r.total}` : 'Reading';
}

export const READING_HINT = 'Documents are read one at a time on this server. Scanned pages need OCR, about '
  + '10 seconds a page; the list updates by itself.';

/** The project or candidate a related document belongs to (Experience: work_order_id; HR: cv_id). */
export function parentOf(d: LibraryDoc): string {
  return String(d.metadata['work_order_id'] ?? d.metadata['cv_id'] ?? '');
}

/** The bid a document was uploaded for (its EMD, power of attorney…), or '' for a library document. */
export function bidOf(d: LibraryDoc): string {
  return String(d.metadata['bid_id'] ?? '');
}

/** Types of the documents uploaded for one bid. */
export const BID_DOC_TYPES: [string, string][] = [
  ['emd', 'EMD / bid security'], ['tender_fee', 'Tender fee receipt'], ['power_of_attorney', 'Power of attorney / board resolution'],
  ['bid_form', 'Signed bid form'], ['integrity_pact', 'Integrity pact'], ['undertaking', 'Undertaking / declaration'], ['other', 'Other'],
];

/**
 * Why two candidates are probably the same person, or '': the same UTL code, or names where every word of the
 * shorter (two words at least) is in the longer, ignoring case and punctuation ("Anil Kumar" ~ "ANIL KUMAR YADAV").
 */
export function samePersonReason(a: LibraryDoc, b: LibraryDoc): string {
  const code = (d: LibraryDoc) => poKey(d.metadata['employee_code']);
  if (code(a) && code(a) === code(b)) return 'same UTL code';
  const words = (d: LibraryDoc) => String(d.metadata['name'] ?? '').toLowerCase().replace(/[^a-z\s]/g, ' ').split(/\s+/).filter(w => w.length > 1);
  const [x, y] = [words(a), words(b)].sort((p, q) => p.length - q.length);
  return x.length >= 2 && x.every(w => y.includes(w)) ? 'similar name' : '';
}

export interface BidSummary {
  id: string;
  title: string;
  rfpRef: string | null;
  department: string | null;
  dueAt: string | null;
  status: string;
  error: string | null;
  outputPageCount: number | null;
  hasPdf: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface SectionDocRef {
  id: string; title: string; pageCount: number | null; docType: string; category: Category;
  validUntil: string | null; status: string;
}

export interface Section {
  id: string; position: number; kind: 'cover' | 'index' | 'text' | 'docs'; title: string; html: string;
  include: boolean; sources: string[]; documents: SectionDocRef[]; updatedAt?: string;
}

export interface JobInfo {
  id: number; type: string; status: 'queued' | 'running' | 'awaiting_approval' | 'done' | 'failed';
  error: string | null; waitingEgressId: string | null; createdAt: string; finishedAt: string | null;
}

/** Set when a person confirmed or changed a result; the automatic one is then kept in auto. */
export interface Decided { by: string; at: string; }

export interface PqResult {
  clause: string; text: string; category: string;
  status: 'met' | 'partial' | 'gap' | 'review' | 'undertaking'; evidence: string[]; note: string;
  decided?: Decided; auto?: { status: string; evidence: string[]; note: string };
}

export interface PersonResult {
  role: string; qualification: string; min_years: number | null; certifications: string[];
  status: string; document_id: string | null; name: string | null; years: number | null; note: string;
  decided?: Decided; auto?: { status: string; document_id: string | null; name: string | null };
}

/** A person's eligibility decision: the result, and the documents (PQ) or CV (key personnel) it rests on. */
export interface EligibilityDecision { status?: string; evidence?: string[]; documentId?: string | null; note?: string; reset?: boolean; }

export interface PqCriterion {
  clause: string; text: string; category: string; threshold_value: number | null; threshold_unit: string | null;
  window_years: number | null; evidence_expected: string;
}

/** Stage 1 (RFP analysis) fills extracted/documents; stage 2 (bid response) adds match. */
export interface Analysis {
  extracted?: {
    meta: Record<string, string | null>;
    amendments?: { document: string; field: string; change: string }[];
    pq_criteria: PqCriterion[];
    tech_criteria: { clause?: string; parameter: string; max_marks: number | null; basis: string }[];
    personnel: { role: string; qualification: string; min_years: number | null; certifications: string[] }[];
    forms: { code: string; title: string }[];
  };
  edited?: Record<string, string | null>;
  documents?: { name: string; kind: string; path: string; pages: number }[];
  match?: { pq: PqResult[]; personnel: PersonResult[]; summary: Record<string, number>; reference_date: string };
  rfp_pages?: number;
  sections_sent?: string[];
  analysed_at?: string;
  matched_at?: string;
}

export interface RfpFile { path: string; name: string; kind?: string; uploaded_at?: string; }

/** The bid folder written with each PDF build: the complete bid, each part as its own PDF, the RFP documents. */
export interface BidFolder {
  folder?: string;
  files: { name: string; title: string; kind: 'complete' | 'section' | 'criterion' | 'document' | 'rfp'; pages: number | null; size: number }[];
  zip_size?: number;
  built_at?: string;
}

export interface BidDetail {
  bid: BidSummary;
  analysis: Analysis;
  settings: BidSettings;
  rfpFiles: RfpFile[];
  sections: Section[];
  jobs: JobInfo[];
}

/** Kinds of document one tender can have; corrigenda, addenda and clarifications amend the others. */
export const RFP_DOC_KINDS: Record<string, string> = {
  rfp: 'RFP', corrigendum: 'Corrigendum', addendum: 'Addendum', clarification: 'Clarification / pre-bid replies',
  annexure: 'Annexure', other: 'Other',
};

/** A first guess from the file name; the person can change it before uploading. */
export function guessRfpKind(fileName: string): string {
  const n = fileName.toLowerCase();
  if (/corrigend/.test(n)) return 'corrigendum';
  if (/addend|amendment/.test(n)) return 'addendum';
  if (/clarif|pre[-_ ]?bid|query|queries|replies|response to/.test(n)) return 'clarification';
  if (/annex|appendix|schedule|format|form/.test(n)) return 'annexure';
  return 'rfp';
}

/** The tender's facts on the RFP analysis screen, by group: [key, label]. */
export const RFP_FACTS: { group: string; fields: [string, string][] }[] = [
  { group: 'Key dates', fields: [['publish_date', 'RFP published'], ['prebid_meeting', 'Pre-bid meeting'],
    ['prebid_query_deadline', 'Pre-bid queries by'], ['bid_due', 'Bid due'], ['tech_bid_opening', 'Technical bid opening'],
    ['presentation_date', 'Presentation'], ['fin_bid_opening', 'Financial bid opening']] },
  { group: 'Commercials', fields: [['estimated_budget', 'Estimated budget'], ['emd', 'EMD'], ['emd_exemption', 'EMD exemption'],
    ['tender_fee', 'Tender fee'], ['performance_security', 'Performance security']] },
  { group: 'Evaluation and terms', fields: [['selection_method', 'Selection method'], ['min_technical_score', 'Minimum technical score'],
    ['bid_validity', 'Bid validity'], ['contract_period', 'Contract period'], ['submission_mode', 'Submission']] },
];

export interface BidSettings {
  header?: { enabled: boolean; left: string; right: string };
  page_numbers?: { enabled: boolean; format: string; position: string; start: number; skip_cover: boolean };
  /** image_id / stamp_id: the saved signature and stamp images chosen (image_path: a signature uploaded for the bid
   * by an earlier version, used while none is chosen). */
  sign?: {
    enabled: boolean; image_id?: string | null; image_path?: string | null; caption: string;
    stamp: boolean; stamp_id?: string | null; position: 'left' | 'right'; apply: 'all' | 'not_cover' | 'current' | 'range'; range: string;
  };
}

/** A saved signature or stamp image, reused across bids. */
export interface SignImage { id: string; kind: 'signature' | 'stamp'; name: string; createdAt: string; }

export interface User { id: string; email: string; fullName: string; role: 'admin' | 'bid_manager' | 'reviewer'; }

export const CATEGORIES: { key: Category; label: string; types: string[] }[] = [
  { key: 'experience', label: 'Experience', types: ['work_order', 'completion_certificate', 'performance_certificate', 'evidence', 'project_rfp', 'other'] },
  { key: 'statutory', label: 'Statutory', types: ['incorporation', 'moa_aoa', 'pan', 'gst', 'certification', 'ca_certificate', 'financial_statement', 'power_of_attorney', 'manpower_certificate', 'epf_esic', 'undertaking', 'other'] },
  { key: 'hr', label: 'Human resources', types: ['cv', 'certificate', 'manpower_certificate', 'other'] },
  { key: 'literature', label: 'Bid literature', types: ['company_profile', 'approach', 'security', 'o_and_m', 'other'] },
  { key: 'uploads', label: 'Uploaded', types: ['emd', 'tender_fee', 'power_of_attorney', 'bid_form', 'integrity_pact', 'undertaking', 'other'] },
];

/** Names shown for document types in the Library. */
export const DOC_TYPE_LABELS: Partial<Record<string, string>> = {
  incorporation: 'Certificate of Incorporation', moa_aoa: 'MOA & AOA', pan: 'PAN Card', gst: 'GST Registration', certification: 'Certification',
  ca_certificate: 'CA Certificate', financial_statement: 'Audited Financial Statements', power_of_attorney: 'Power of Attorney',
  manpower_certificate: 'Manpower Certificate', epf_esic: 'EPF / ESIC Registration', undertaking: 'Undertaking / Declaration',
  emd: 'EMD / Bid security', tender_fee: 'Tender fee receipt', bid_form: 'Signed bid form', integrity_pact: 'Integrity pact',
  work_order: 'Work order / PO', completion_certificate: 'Completion certificate', performance_certificate: 'Performance certificate',
  evidence: 'Documentary evidence', project_rfp: 'RFP / tender document', cv: 'CV', certificate: 'Certificate', other: 'Other',
};

/** Suggestions for a certificate's standard (any other text can be typed). */
export const CERT_STANDARDS = ['CMMI', 'ISO 9001:2015', 'ISO/IEC 27001:2022', 'ISO/IEC 20000-1:2018', 'ISO 14001:2015',
  'ISO 45001:2018', 'ISO 22301:2019', 'ISO/IEC 27701:2019'];

/** States and union territories, for a GST registration (ingestion reads it from the GSTIN's state code). */
export const INDIAN_STATES = ['Andaman and Nicobar Islands', 'Andhra Pradesh', 'Arunachal Pradesh', 'Assam', 'Bihar', 'Chandigarh',
  'Chhattisgarh', 'Dadra and Nagar Haveli and Daman and Diu', 'Delhi', 'Goa', 'Gujarat', 'Haryana', 'Himachal Pradesh',
  'Jammu and Kashmir', 'Jharkhand', 'Karnataka', 'Kerala', 'Ladakh', 'Lakshadweep', 'Madhya Pradesh', 'Maharashtra', 'Manipur',
  'Meghalaya', 'Mizoram', 'Nagaland', 'Odisha', 'Puducherry', 'Punjab', 'Rajasthan', 'Sikkim', 'Tamil Nadu', 'Telangana',
  'Tripura', 'Uttar Pradesh', 'Uttarakhand', 'West Bengal'];

/** Suggested tags for Experience projects (the RFP extraction prefers the same list). */
export const PROJECT_TAGS = ['Manpower', 'AMC', 'Payment Gateway', 'Software Development', 'Mobile App', 'Portal', 'ERP',
  'Data Centre', 'Cloud', 'Networking', 'Cyber Security', 'Helpdesk', 'Facility Management', 'Digitisation', 'GIS',
  'Analytics', 'Training', 'Hardware Supply', 'O&M'];

/** Detail fields the matcher reads, per document type (see README). */
export const DETAIL_HINTS: Record<string, string> = {
  incorporation: '{"cin": "...", "incorporation_year": 1995}',
  ca_certificate: '{"turnover_cr": {"2023-24": 98.4, "2024-25": 112.6, "2025-26": 126.9}, "net_worth_cr": {"2023-24": 61.2, "2024-25": 68.8, "2025-26": 77.1}}',
  work_order: '{"client": "...", "project": "...", "value_cr": 12.6, "period": "Jan 2022 – Dec 2025", "end_year": 2025, "completed": true, "scope": "..."}',
  completion_certificate: '{"work_order_id": "<id of the work order in the library>"}',
  manpower_certificate: '{"headcount": 412}',
  pan: '{"pan": "..."}',
  gst: '{"gstin": "..."}',
};

export interface PageMap {
  pages: { kind: 'cover' | 'index' | 'text' | 'divider' | 'docs' }[];
  sections: { id: string; title: string; kind: string; start: number; count: number }[];
  numbering: { start: number; skip_cover: boolean };
  built_at: string;
  dsc?: DscInfo;          // set when the built PDF has been signed with a DSC token
}

/** Who signed the bid's PDF with a DSC token, and when (ai-service pdf/dsc.py). */
export interface DscInfo { signer: string; issuer: string; signed_at: string; valid_to: string; serial: string; }

export interface Company { name: string; city: string; cin: string; }
