# BidCraft – Bid Response Crafter

An on-premise application that turns an organisation's experience, statutory, HR and
literature documents into a complete technical bid for a government RFP: eligibility check,
editable draft, annexures, and one page-numbered, signed and stamped PDF.

The complete document repository stays on your Ubuntu server. Only short, redacted extracts
are sent to the external model, and every outbound request is recorded and listed in the
audit log.

```
 YOUR ON-PREMISE SERVER (Ubuntu 24.04, no Docker)
 ┌────────────────────────────────────────────────────────────────────────────────┐
 │  Angular web app  ──nginx──▶  .NET 8 API  ──jobs table──▶  Python worker        │
 │                                   │                            │               │
 │                              PostgreSQL 16 + pgvector          │               │
 │                              documents, chunks, bids,          │               │
 │                              jobs, egress log, vault           │               │
 │                                   │                            │               │
 │                              /srv/bidcraft/files  ◀────────────┘               │
 │                              (uploads, PDFs, output)                            │
 │                                                                                 │
 │  Local RAG + vector DB: text extraction, OCR, embeddings, search – all local    │
 │  Data minimisation / redaction: privacy gateway (app/privacy/gateway.py)        │
 └──────────────────────────────────────┬──────────────────────────────────────────┘
                                        │  limited, redacted request (HTTPS)
                                        ▼
                                   OpenAI API
                   processes only the selected, redacted extracts
```

Even redacted text is visible to OpenAI while it is processed. Minimisation reduces exposure;
it does not eliminate it. Check OpenAI's current data-handling terms for API traffic and ask
for zero-data-retention if your contracts require it.

## What leaves the server, and what never does

| Stays on the server | Sent out (redacted, size-capped) |
|---|---|
| Every uploaded file and its text | Selected RFP sections (eligibility, evaluation, personnel, formats, key dates) |
| Embeddings and search index | Up to ~10 short extracts from the library for the narrative section |
| Compliance matching (rules on your metadata) | The drafting instruction and system prompt |
| Templates for letters, tables and undertakings | |
| PDF assembly, numbering, signing, stamping | |
| The map from `[[PERSON_1]]`-style tokens to real values (encrypted) | |

Redaction (`ai-service/app/privacy/redactor.py`): PAN, GSTIN, IFSC, e-mail, phone, names from
your HR library and "Mr./Ms./Shri ..." patterns are replaced by tokens. Aadhaar numbers and bank
account numbers block the request. Documents marked **restricted** are never used as evidence;
**confidential** ones are sent redacted like the rest. All of this is set in
`ai-service/config/redaction_policy.yaml`.

Every outbound request is stored in `egress_requests` exactly as sent, with counts of redactions
and the list of sources included or dropped; there is no approval step.

## Components

| Part | Technology | Role |
|---|---|---|
| `web/` | Angular 21, pdf.js (legacy build), Lucide icons, Source Sans/Serif fonts | Library, RFP analysis, drag-and-drop bid builder, Acrobat-style preview with sign/stamp settings, audit log |
| `api/` | ASP.NET Core 8, EF Core, Npgsql | Users and roles, uploads, bids and sections, settings, audit; enqueues jobs |
| `ai-service/` | Python 3.12, FastAPI, fastembed, pgvector, WeasyPrint, pikepdf, reportlab, pyHanko | Internal search API; worker for ingestion, RFP analysis, drafting and PDF assembly; privacy gateway |
| `db/schema.sql` | PostgreSQL 16 + pgvector | Single database for data, vectors, job queue and audit |
| `deploy/` | bash, systemd, nginx | Installer and service definitions |

## Install (Ubuntu 24.04)

1. Copy this directory to the server (the `web/dist` folder contains a prebuilt web app, so the
   server does not need Node.js).
2. `sudo bash deploy/install.sh`
3. Edit `/etc/bidcraft/bidcraft.env`: set `OPENAI_API_KEY`, `OPENAI_MODEL`, `Admin__Email`,
   `COMPANY_NAME`, `COMPANY_CITY`, `COMPANY_CIN`. The installer prints the generated admin password.
4. `sudo systemctl restart bidcraft-api bidcraft-ai bidcraft-worker`
5. Replace the self-signed certificate in `/etc/bidcraft/tls/` with one from your internal CA.
6. Open `https://<server>/` and sign in. Create reviewer and bid-manager users under
   `POST /api/users` (admin only) or directly in the `users` table with a bcrypt hash.

Services: `bidcraft-api` (port 5080, local), `bidcraft-ai` (port 8001, local), `bidcraft-worker`.
Logs: `journalctl -u bidcraft-worker -f`. Health: `curl -k https://localhost/api/health`.

### Restricting outbound traffic

The worker is the only process that needs to reach the internet. A simple allow-list with ufw:

```
ufw default deny outgoing
ufw allow out to any port 53
ufw allow out to any port 443 proto tcp   # or restrict to the IP ranges of api.openai.com
ufw allow in 443/tcp
```

Pip, apt and the embedding-model download need outbound access during installation; run the
installer before tightening the rules, or mirror the packages internally.

## Using it

1. **Documents Library** – upload work orders, completion certificates, statutory documents,
   CVs and reusable write-ups. Each file is converted to PDF, OCR'd if scanned, indexed locally and
   given a suggested type and details. Open a row to confirm the details the matcher relies on.
2. **RFP analysis** – upload all documents of the tender (RFP volumes, annexures, corrigenda,
   addenda, pre-bid clarifications), each with its type. The worker indexes them, picks the bid data,
   scope, eligibility, evaluation, personnel and format sections of the main documents, takes the
   amendments whole, and sends them redacted for extraction. The screen shows the project title,
   department, scope, key dates (published, pre-bid, bid due, openings, presentation), EMD, tender fee,
   estimated budget, selection method, the PQ and TEC criteria, key personnel, formats and what each
   corrigendum changed. Correct any fact with *Edit facts* (kept on re-analysis); add a corrigendum when
   it is issued and the RFP is analysed again.
3. **Bid response** – *Check eligibility* matches each criterion against the library with plain rules
   (nothing is sent) and shows it as Met, Needs attention, Gap, Confirm or Undertaking, with the
   evidence found. Run it again after adding documents to the library. Choose *Generate bid draft*.
4. **Bid builder** – reorder sections by dragging, edit the text, fill the yellow fields,
   add or remove annexure documents from the library or your device.
5. **Preview & sign** – the server builds the pages once; the viewer shows them with thumbnails,
   zoom and bookmarks, and draws the header, page numbers, signature and seal on top live, so every
   change is visible immediately. Changing the starting number or the cover rule rebuilds the index
   and cross-references automatically. *Export PDF* runs the pre-submission check and generates the
   final stamped PDF on the server, identical to what was on screen.
6. **Bid folder** – every build also writes a folder per RFP (`files/bids/<id>/folder`, listed on the
   Bid response screen and downloadable as one ZIP): the complete signed bid, each section and each
   annexure document (ROC, PAN, GST, certifications, work orders…) as its own numbered PDF cut from the
   stamped pages, and the RFP documents. The draft always attaches the Certificate of Incorporation,
   PAN, every GST registration and the certifications the RFP's criteria name (valid on the bid due
   date), plus whatever the eligibility check cites. Each PQ and each TEC criterion gets a separator page
   (clause, criterion, compliance, documents asked for, supporting documents with page references) followed
   by its documents; a document is enclosed once, after the first criterion needing it, and later
   separators refer to its pages. In the bid folder, a criterion's separator and documents make one file.

The screens are a port of the approved HTML prototype (`styles.css` is the prototype's own CSS),
and the PDF engine uses the prototype's page geometry and fonts (`ai-service/fonts`), so the
on-screen preview and the exported pages match.

## Signature, stamp and DSC

*Preview & sign → Sign & stamp* puts a signature image and a stamp image on the pages chosen under *Apply to*.
Images are uploaded once and saved for every bid (`sign_images`, `api/sign-images`); a PNG with a transparent
background prints cleanly.

*Digital signature (DSC)* signs the built PDF with a Class 3 DSC on a USB token: one signature over the whole file,
and each signed page notes "Digitally signed by … on …". The token's key never leaves the person's computer:

- `signer/` is **BidCraft Signer**, a small Windows app (`dotnet publish -c Release -o ../web/public/downloads`
  makes the self-contained `BidCraftSigner.exe` offered for download on the screen). It listens on
  `http://localhost:47821`, answers only the BidCraft site (`BidCraftSigner.origins.txt` next to the exe, one
  address per line; `https://192.168.2.141:8443` otherwise), lists the signing certificates the token's driver
  puts in the Windows certificate store, and signs one SHA-256 hash after a Yes/No confirmation; the driver asks
  for the PIN. The exe is not code-signed: Windows SmartScreen may ask before it runs the first time.
- `ai-service/app/pdf/dsc.py` prepares the PDF and the hash (pyHanko), checks the token's signature against the
  certificate, embeds it, verifies the result and replaces the bid's PDF and the complete file of the bid folder.

Building the PDF again removes the DSC signature (the screen asks first); sign again afterwards.

## Redrafting a section

In the builder, *Redraft* on a text section sends the current draft plus a few RFP and library
extracts through the privacy gateway with your instruction and tone. Fill-in fields and page
references are preserved.

## Document details the matcher reads

Enter these in the **Details** box of a document (JSON). Values the ingestion suggested are
listed under "filled automatically" until you save.

| doc_type | Details | Used for |
|---|---|---|
| `incorporation` | `{"cin": "...", "incorporation_year": 1995}` | years in operation |
| `pan`, `gst` | `{"pan": "..."}`, `{"gstin": "..."}` | tax registration; bidder profile |
| `ca_certificate` | `{"turnover_cr": {"2023-24": 98.4, ...}, "net_worth_cr": {...}}` | average turnover, positive net worth |
| `certification` + *Valid until* | `{"standard": "ISO 27001:2022"}` or `{"standard": "CMMI", "level": 5}` | required certifications, validity on the due date |
| `work_order` | `{"short_name": "...", "client": "...", "project": "...", "ministry": "...", "scope": "...", "value_cr": 12.6, "po_date": "2022-01-10", "duration": "36 months", "completion_date": "2025-01-09", "domains": ["AMC", "Manpower"]}` (set on the project page; `end_year`, `completed` and `period` are derived on save) | project experience thresholds, citations |
| `completion_certificate` | `{"work_order_id": "<library id of the work order>"}` | pairs the certificate with its order |
| `cv` | `{"name": "...", "role": "Project Manager", "total_years": 19}` | key personnel; names are redacted outbound |
| `manpower_certificate` | `{"headcount": 412}` | minimum staff strength |
| `power_of_attorney` | – | attached as the last annexure |
| `company_profile` (literature) | – | first paragraphs go into the bidder profile |
| any statutory type | `{"number": "QMS/IN/2024/0123"}` | *Document number* column of the Statutory tab |

**Experience projects.** Each file added on the Experience tab starts a project from its purchase order /
work order. The project page holds its details, related documents (completion and performance
certificates, documentary evidence) and the project's RFP; related documents carry
`{"work_order_id": "<project id>"}` and are deleted with the project. After the purchase order, a linked
completion certificate or the project's RFP has been read, the `extract_project` job sends it, redacted,
through the privacy gateway (purpose `po_extraction` for the order and certificate, `project_extraction`
for the RFP's opening pages and scope / background sections) and fills short name, title, client,
state / ministry, description, value, PO date, duration, completion date and tags. It fills fields that
are empty or were filled automatically before; values a person entered are never replaced.

**CVs.** After a CV in the Human resources library has been read, the `extract_cv` job fills name,
proposed role (`role`), total experience (`total_years`), the two highest qualifications (`qualification`, e.g. "MBA, B.Tech"),
`skills` and `certifications` (purpose `cv_extraction`). Lines with personal identifiers (Aadhaar, PAN,
passport, date of birth, family, address, account numbers) are dropped first, and the person's name
(from the CV and its file name), e-mail and phone are tokenised by the gateway. Each CV opens as a candidate
page with a manually kept **UTL code** and the candidate's certificates: uploaded there under a name
(e.g. PMP, which also joins the candidate's certifications) or attached from the HR library, linked by
`{"cv_id": "<CV id>"}` and deleted with the candidate. Candidates with the same UTL code, or names where
every word of the shorter is in the longer, are flagged as probable duplicates.

**Statutory labels.** Certificates show their standard ("CMMI Level 5", "ISO 9001:2015", read from the
text or the file name) and GST registrations their state, from the GSTIN's state code.

**Duplicates.** Uploads record the file's SHA-256 (`library_documents.file_sha256`; documents added
earlier get it when they are next read). A file identical to one already in the library is not added
again: `POST /api/library` returns `{created, duplicates}` and the screens name the existing copy (the bid
builder attaches it). Experience projects sharing a PO number (compared without spaces, punctuation or
case) are flagged *Duplicate PO no.*, since a second scan of the same order has a different file hash.

For statutory documents, ingestion fills `number` from the CIN, PAN, GSTIN, UDIN, EPF/ESIC code or a
labelled certificate/registration number, and *Valid until* from "valid till", "expiry date" or
"valid from … to …" in the text. Documents added before this was introduced pick these up after
**Read again**.

Criteria the rules cannot decide (for example domain-specific experience) fall back to local
search and are marked **Confirm** with the candidate documents.

## Outbound policy (`redaction_policy.yaml`)

- `limits` – maximum extracts, characters per extract and per request.
- `entities` – `redact`, `block` or `allow` per entity type.
- `custom_terms` – words always redacted (client code names, internal project names).

`POST /redaction/preview` on the AI service lets an admin test the policy on any
text without sending anything.

## Running the Python tests

```
cd ai-service && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m pytest tests
```

## Known limits and next steps

- **Class 3 DSC on a USB token** cannot be used by the server. `SIGN_P12_PATH` supports an
  organisational PKCS#12 certificate; token signing needs a small local helper on the signer's
  PC (PKCS#11), which is not included.
- The financial bid (FIN forms, BoQ) is intentionally outside this document; it can follow the
  same section and assembly flow as a second envelope.
- Pages with a `/Rotate` entry in scanned annexures receive the overlay in unrotated
  coordinates; re-save such scans upright before uploading.
- Metadata extraction from scans is heuristic; confirm the details before relying on a match.
- The .NET API was written against Npgsql.EntityFrameworkCore.PostgreSQL 8.0.8,
  EFCore.NamingConventions 8.0.3, JwtBearer 8.0.8 and BCrypt.Net-Next 4.0.3. It could not be
  compiled in the environment where this package was produced (no NuGet access), so run
  `dotnet build api` once before deploying and fix anything the compiler reports.
