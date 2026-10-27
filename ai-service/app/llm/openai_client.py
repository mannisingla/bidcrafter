"""Thin wrapper over the OpenAI API. Only app/privacy/gateway.py may call this module."""
from __future__ import annotations

from functools import lru_cache

from openai import OpenAI

from ..config import get_settings


@lru_cache(maxsize=1)
def _client() -> OpenAI:
    s = get_settings()
    if not s.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    return OpenAI(api_key=s.openai_api_key, base_url=s.openai_base_url, timeout=180, max_retries=2)


def complete(system: str, user: str, schema: dict | None = None, schema_name: str = "result") -> tuple[str, dict]:
    """Send one request. Returns (content, metadata). With a schema, content is JSON text."""
    kwargs: dict = {}
    if schema is not None:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "schema": schema, "strict": True},
        }
    response = _client().chat.completions.create(
        model=get_settings().openai_model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        store=False,
        **kwargs,
    )
    message = response.choices[0].message
    if getattr(message, "refusal", None):
        raise RuntimeError(f"The model declined the request: {message.refusal}")
    usage = response.usage.model_dump() if response.usage else {}
    return message.content or "", {"model": response.model, "id": response.id, "usage": usage}
