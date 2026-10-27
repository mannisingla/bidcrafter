"""Internal HTTP API of the AI service. Bound to 127.0.0.1; called only by the .NET API."""
from __future__ import annotations

import base64
import binascii
import hmac
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .config import get_settings, load_policy
from .db import connect
from .drafting.sanitize import clean_html
from .pdf import dsc
from .pdf.assembler import merged_settings
from .privacy.gateway import known_people
from .privacy.redactor import Redactor
from .rag import store

app = FastAPI(title="BidCraft AI service", docs_url=None, redoc_url=None, openapi_url=None)


def require_token(x_internal_token: str = Header(default="")) -> None:
    if not hmac.compare_digest(x_internal_token, get_settings().internal_token):
        raise HTTPException(status_code=401, detail="Invalid internal token")


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    categories: list[str] | None = None
    k: int = Field(default=10, ge=1, le=50)


class RedactRequest(BaseModel):
    text: str = Field(max_length=50_000)


@app.get("/health")
def health() -> dict:
    with connect() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}


@app.post("/search", dependencies=[Depends(require_token)])
def search(req: SearchRequest) -> dict:
    with connect() as conn:
        hits = store.search(conn, req.query, categories=req.categories, k=req.k)
    return {"results": [{
        "documentId": str(h["document_id"]), "title": h["title"], "category": h["category"],
        "docType": h["doc_type"], "pageFrom": h["page_from"], "pageTo": h["page_to"],
        "snippet": h["content"][:500], "similarity": round(h["similarity"], 3),
    } for h in hits]}


class SanitizeRequest(BaseModel):
    html: str = Field(default="", max_length=2_000_000)


@app.post("/sanitize", dependencies=[Depends(require_token)])
def sanitize(req: SanitizeRequest) -> dict:
    """Section HTML saved from the bid builder, cleaned with the same whitelist the PDF build uses."""
    return {"html": clean_html(req.html)}


@app.post("/redaction/preview", dependencies=[Depends(require_token)])
def redaction_preview(req: RedactRequest) -> dict:
    """Shows what the gateway would send for a piece of text. Nothing leaves the server."""
    policy = load_policy()
    with connect() as conn:
        people = known_people(conn)
    r = Redactor(actions=policy.get("entities", {}), people=people, custom_terms=policy.get("custom_terms") or [])
    return {"redacted": r.redact(req.text), "summary": r.summary()}


class DscPrepareRequest(BaseModel):
    bid_id: uuid.UUID
    certificate: str = Field(max_length=20_000)                 # base64 DER, from the BidCraft Signer app
    chain: list[str] = Field(default_factory=list, max_length=8)


class DscFinishRequest(BaseModel):
    bid_id: uuid.UUID
    session: str = Field(max_length=100)
    signature: str = Field(max_length=4_000)                    # base64, made by the DSC token


def _b64(text: str) -> bytes:
    try:
        return base64.b64decode(text, validate=True)
    except binascii.Error as exc:
        raise HTTPException(status_code=400, detail="The certificate or signature from the signer app could not be read") from exc


@app.post("/dsc/prepare", dependencies=[Depends(require_token)])
def dsc_prepare(req: DscPrepareRequest) -> dict:
    """Digital signature with a DSC token, step 1: the hash for the token to sign (pdf/dsc.py)."""
    with connect() as conn:
        bid = conn.execute("SELECT settings FROM bids WHERE id = %s", (req.bid_id,)).fetchone()
    if bid is None:
        raise HTTPException(status_code=404, detail="Bid not found")
    try:
        return dsc.prepare(get_settings().files_root / "bids" / str(req.bid_id), merged_settings(bid["settings"]),
                           _b64(req.certificate), [_b64(c) for c in req.chain])
    except dsc.SigningError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/dsc/finish", dependencies=[Depends(require_token)])
def dsc_finish(req: DscFinishRequest) -> dict:
    """Step 2: the token's signature goes into the PDF, which is verified and replaces the bid's PDF."""
    try:
        return dsc.finish(get_settings().files_root / "bids" / str(req.bid_id), req.session, _b64(req.signature))
    except dsc.SigningError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
