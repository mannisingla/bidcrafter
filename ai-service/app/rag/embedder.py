"""Local embeddings (ONNX via fastembed). Document text is never sent out to be embedded."""
from __future__ import annotations

from functools import lru_cache

from fastembed import TextEmbedding

from ..config import get_settings

DIMENSIONS = 384   # must match vector(384) in db/schema.sql
# The server is shared: fastembed's default batch of 256 long chunks needs gigabytes of working memory, which
# ONNX keeps once taken. Small batches and two threads keep a 100-page RFP within ~100 MB and leave CPU for others.
BATCH_SIZE = 16
THREADS = 2


@lru_cache(maxsize=1)
def _model() -> TextEmbedding:
    s = get_settings()
    return TextEmbedding(model_name=s.embed_model, cache_dir=str(s.embed_cache), threads=THREADS)


def embed_passages(texts: list[str]):
    return list(_model().passage_embed(texts, batch_size=BATCH_SIZE))


def embed_query(text: str):
    return next(iter(_model().query_embed(text)))
