"""Convert uploads to PDF and read their text, entirely on this server."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from PIL import Image
from pypdf import PdfReader
from reportlab import rl_config
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from ..config import get_settings

rl_config.useA85 = 0       # images as binary streams, not ASCII85 text a quarter larger: portals cap upload sizes

IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"}
OFFICE_TYPES = {".doc", ".docx", ".odt", ".rtf", ".xls", ".xlsx", ".ods", ".ppt", ".pptx"}


def to_pdf(src: Path) -> Path:
    """Return a PDF for ``src``: itself if already a PDF, otherwise a converted copy beside it."""
    ext = src.suffix.lower()
    if ext == ".pdf":
        return src
    out = src.with_suffix(".converted.pdf")
    if out.exists() and out.stat().st_mtime >= src.stat().st_mtime:
        return out
    if ext in IMAGE_TYPES:
        image_to_pdf(src, out)
    elif ext in OFFICE_TYPES:
        _office_to_pdf(src, out)
    else:
        raise ValueError(f"Unsupported file type: {ext}")
    return out


MAX_IMAGE_FRAMES = 300


def image_to_pdf(src: Path, out: Path) -> None:
    """One A4 page per image frame, image centred and scaled to fit, nothing else on the page. The image is the
    original: a JPEG's own bytes (not re-compressed), other images losslessly at full resolution. This PDF is what
    a bid encloses."""
    page_w, page_h = A4
    margin = 18
    c = canvas.Canvas(str(out), pagesize=A4)
    with Image.open(src) as im:
        frames = min(getattr(im, "n_frames", 1), MAX_IMAGE_FRAMES)
        for i in range(frames):
            im.seek(i)
            iw, ih = im.size
            if im.format == "JPEG" and frames == 1:
                image = ImageReader(str(src))            # reportlab embeds a JPEG file's data as it is
            else:
                image = ImageReader(im.convert("L" if im.mode in ("1", "L", "LA", "I", "I;16") else "RGB"))
            scale = min((page_w - 2 * margin) / iw, (page_h - 2 * margin) / ih)
            w, h = iw * scale, ih * scale
            c.drawImage(image, (page_w - w) / 2, (page_h - h) / 2, w, h)
            c.showPage()
    c.save()


def _office_to_pdf(src: Path, out: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "HOME": tmp}
        subprocess.run(
            ["soffice", "--headless", "--norestore", "--convert-to", "pdf", "--outdir", tmp, str(src)],
            check=True, timeout=300, env=env, capture_output=True,
        )
        produced = Path(tmp) / f"{src.stem}.pdf"
        if not produced.exists():
            raise RuntimeError("LibreOffice did not produce a PDF")
        shutil.move(str(produced), out)


def _read(pdf: Path) -> list[str]:
    return [(page.extract_text() or "") for page in PdfReader(str(pdf)).pages]


# A page with images and little text often carries its key facts in the images: certificates print the
# appraisal / certificate number, level and expiry as badges or seals.
SPARSE_PAGE_CHARS = 1000
MAX_SUPPLEMENT_PAGES = 20
SCANNED_PAGE_CHARS = 40

# Pages are rendered one at a time, no larger than A4 at 300 dpi whatever size the page claims to be (a scan saved
# with a 72-dpi page size or a phone photo otherwise rasterises to 100+ megapixels and a gigabyte of memory).
OCR_MAX_SIDE = 3508
OCR_JOBS = 2
OCR_PAGE_TIMEOUT = 300

Progress = Callable[[int, int], bool]


class Cancelled(Exception):
    """Reading stopped because the progress callback said so (the document was deleted meanwhile)."""


def _has_images(page) -> bool:
    try:
        return bool(page.images)
    except Exception:  # noqa: BLE001 - image data pypdf cannot read: OCR will see what is there
        return True


def _pages_to_ocr(pdf: Path, pages: list[str]) -> list[int]:
    """Scanned documents: every page without a text layer. Short documents: also the pages with images and little
    text. Other pages already have their text."""
    if sum(len(p.strip()) for p in pages) / len(pages) < SCANNED_PAGE_CHARS:
        return [i for i, text in enumerate(pages) if len(text.strip()) < SCANNED_PAGE_CHARS]
    if len(pages) > MAX_SUPPLEMENT_PAGES:
        return []
    return [i for i, (page, text) in enumerate(zip(PdfReader(str(pdf)).pages, pages))
            if len(text.strip()) < SPARSE_PAGE_CHARS and _has_images(page)]


def _trim(image: Path) -> None:
    """Greyscale, cropped to the content. A photo placed on a white page otherwise defeats Tesseract's thresholding:
    it separates the white margin from the grey paper, and the text goes black with the paper. (So no white border
    is added back either: even 20 pixels of it has that effect.)"""
    with Image.open(image) as im:
        grey = im.convert("L")
    box = grey.point(lambda p: 255 if p < 245 else 0).getbbox()
    if box:
        grey = grey.crop(box)
    grey.save(image)


def _ocr_page(pdf: Path, number: int, languages: str, tmp: str) -> str:
    prefix = Path(tmp) / f"page{number}"
    subprocess.run(["pdftoppm", "-f", str(number), "-l", str(number), "-singlefile", "-gray", "-png",
                    "-scale-to", str(OCR_MAX_SIDE), str(pdf), str(prefix)],
                   check=True, timeout=OCR_PAGE_TIMEOUT, capture_output=True)
    image = prefix.with_suffix(".png")
    try:
        _trim(image)
        out = subprocess.run(["tesseract", str(image), "stdout", "-l", languages],
                             check=True, timeout=OCR_PAGE_TIMEOUT, capture_output=True,
                             env={**os.environ, "OMP_THREAD_LIMIT": "1"})    # OCR_JOBS processes, one thread each
    finally:
        image.unlink(missing_ok=True)
    return out.stdout.decode("utf-8", errors="replace")


def _ocr_pages(pdf: Path, numbers: list[int], progress: Progress | None) -> dict[int, str]:
    """OCR text of the given pages (0-based). A page that cannot be read (e.g. an encrypted PDF) gives ''."""
    languages = get_settings().ocr_languages
    if progress and not progress(0, len(numbers)):
        raise Cancelled
    texts: dict[int, str] = {}
    with tempfile.TemporaryDirectory() as tmp, ThreadPoolExecutor(OCR_JOBS) as pool:
        futures = {pool.submit(_ocr_page, pdf, i + 1, languages, tmp): i for i in numbers}
        for done, future in enumerate(as_completed(futures), start=1):
            try:
                texts[futures[future]] = future.result()
            except (subprocess.SubprocessError, OSError):
                texts[futures[future]] = ""
            if progress and not progress(done, len(numbers)):
                pool.shutdown(cancel_futures=True)      # pages already being read finish; the rest are dropped
                raise Cancelled
    return texts


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def supplement(text: str, ocr: str) -> str:
    """The page's text plus the OCR lines it does not already contain (text that is only in images)."""
    have = _norm(text)
    extra = [line.strip() for line in ocr.splitlines() if len(_norm(line)) >= 4 and _norm(line) not in have]
    return text + ("\n" + "\n".join(extra) if extra else "")


def extract_pages(pdf: Path, progress: Progress | None = None) -> tuple[Path, list[str]]:
    """Text per page, read on this server. Pages of a scanned PDF (almost no text layer) are OCR'd; in short
    documents, pages with images and little text get the text found only in those images added.

    ``progress(done, total)`` is told how many of the pages to OCR are done; when it returns False, reading stops
    with Cancelled. Returns the PDF and the page texts.
    """
    pages = _read(pdf)
    if not get_settings().ocr_enabled or not pages:
        return pdf, pages
    numbers = _pages_to_ocr(pdf, pages)
    if numbers:
        ocr = _ocr_pages(pdf, numbers, progress)
        pages = [_with_ocr(text, ocr[i]) if i in ocr else text for i, text in enumerate(pages)]
    return pdf, pages


def _with_ocr(text: str, ocr: str) -> str:
    """A page without a text layer is its OCR text; otherwise OCR adds only the lines the text layer lacks."""
    if len(text.strip()) < SCANNED_PAGE_CHARS:
        return ocr.strip() or text
    return supplement(text, ocr)
