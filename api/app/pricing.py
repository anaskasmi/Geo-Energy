"""Model pricing + cost estimation for the per-IP usage budget (GEO-44).

ONE source of truth for what an agent turn costs in USD, used by :mod:`app.budget` to enforce the
per-IP, per-mode spending cap. Two cost paths:

  * TEXT agent (Gemini via Pydantic-AI) — :func:`text_cost_usd` reads a Pydantic-AI ``RunUsage``
    (input / output / cached token counts) and prices it against the configured ``AGENT_MODEL``.
  * VOICE agent (OpenAI Realtime, audio runs browser↔OpenAI) — :func:`realtime_cost_usd` prices the
    ``usage`` object the browser forwards from each ``response.done`` realtime event (token counts
    broken down by modality: text vs audio, input vs output, cached).

Rates are per 1,000,000 tokens, taken from public pricing (researched 2026-06; see README/PR notes):

  Gemini 3.5 Flash:        in $1.50  · out $9.00  · cached-in $0.15
  gpt-realtime:            text in $5 / out $20 · audio in $32 / out $64 · cached audio-in $0.40
  gpt-4o-mini-transcribe:  audio in $3 · text in $1.25 / out $5   (input-audio transcription)

Unknown models fall back to the Gemini-Flash rates (a safe, non-zero default so the cap still bites).
Costs are deliberately computed with UNCACHED rates when a breakdown is missing — erring toward
charging slightly more, which protects the budget rather than overspending it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PER_MILLION = 1_000_000.0


@dataclass(frozen=True)
class TextRates:
    """USD per 1M tokens for a text model."""

    input: float
    output: float
    cached_input: float = 0.0


# --- Text (Pydantic-AI) model rates, keyed by a substring of the AGENT_MODEL id ----------------
# Matched by `in` against the lowercased model id, longest key first, so "gemini-3.5-flash" wins
# over a bare "gemini". Add a row here when you add a provider/model.
_TEXT_RATES: dict[str, TextRates] = {
    "gemini-3.5-flash": TextRates(input=1.50, output=9.00, cached_input=0.15),
    "gemini-3.1-pro": TextRates(input=2.50, output=15.00, cached_input=0.25),
    "gemini-2.5-flash": TextRates(input=0.30, output=2.50, cached_input=0.075),
    "gemini": TextRates(input=1.50, output=9.00, cached_input=0.15),  # generic Gemini fallback
}
_DEFAULT_TEXT_RATES = TextRates(input=1.50, output=9.00, cached_input=0.15)

# --- Realtime (voice) rates: USD per 1M tokens, per modality -----------------------------------
# gpt-realtime conversation tokens.
RT_TEXT_IN = 5.0
RT_TEXT_OUT = 20.0
RT_AUDIO_IN = 32.0
RT_AUDIO_OUT = 64.0
RT_AUDIO_IN_CACHED = 0.40
# gpt-4o-mini-transcribe (input-audio transcription) — folded into the realtime estimate so the
# voice cap accounts for the transcription side-cost too.
TRANSCRIBE_AUDIO_IN = 3.0


def text_rates_for(model_id: str) -> TextRates:
    """Pick the text rates for a Pydantic-AI model id (provider:model), longest match first."""
    mid = (model_id or "").lower()
    for key in sorted(_TEXT_RATES, key=len, reverse=True):
        if key in mid:
            return _TEXT_RATES[key]
    return _DEFAULT_TEXT_RATES


def _int(value: Any) -> int:
    """Coerce a possibly-missing/None token count to a non-negative int (defensive)."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def text_cost_usd(usage: Any, model_id: str) -> float:
    """USD cost of one text-agent run from a Pydantic-AI ``RunUsage`` and the model id.

    Reads ``input_tokens`` / ``output_tokens`` (with ``request_tokens`` / ``response_tokens`` as
    older-name fallbacks) and discounts any ``cache_read_tokens`` to the cached-input rate. Never
    raises — a missing/odd usage object simply costs 0 (and the cap still works via later turns).
    """
    if usage is None:
        return 0.0
    rates = text_rates_for(model_id)
    inp = _int(getattr(usage, "input_tokens", None) or getattr(usage, "request_tokens", None))
    out = _int(getattr(usage, "output_tokens", None) or getattr(usage, "response_tokens", None))
    cached = min(_int(getattr(usage, "cache_read_tokens", None)), inp)
    fresh_in = inp - cached
    cost = (
        fresh_in * rates.input
        + cached * rates.cached_input
        + out * rates.output
    ) / PER_MILLION
    return max(0.0, cost)


def realtime_cost_usd(usage: dict | None) -> float:
    """USD cost of one realtime (voice) turn from the browser-forwarded ``response.usage`` object.

    The OpenAI Realtime ``response.done`` event reports ``input_tokens`` / ``output_tokens`` plus
    ``*_token_details`` broken down into ``text_tokens`` / ``audio_tokens`` (and ``cached_tokens`` /
    ``cached_tokens_details`` on the input side). We price each modality at its rate and add a small
    transcription charge for the input audio (gpt-4o-mini-transcribe). Untrusted client input, so it
    is parsed defensively and never raises; anything missing simply contributes 0.
    """
    if not isinstance(usage, dict):
        return 0.0

    in_details = usage.get("input_token_details") or {}
    out_details = usage.get("output_token_details") or {}
    if not isinstance(in_details, dict):
        in_details = {}
    if not isinstance(out_details, dict):
        out_details = {}

    # Cached input tokens (charged at the cheaper cached-audio rate); subtracted from fresh audio in.
    cached = in_details.get("cached_tokens")
    cached_detail = in_details.get("cached_tokens_details") or {}
    cached_audio = _int(
        (cached_detail.get("audio_tokens") if isinstance(cached_detail, dict) else None)
        if cached_detail
        else cached
    )

    in_audio = _int(in_details.get("audio_tokens"))
    in_text = _int(in_details.get("text_tokens"))
    out_audio = _int(out_details.get("audio_tokens"))
    out_text = _int(out_details.get("text_tokens"))

    # If a side has no modality breakdown, fall back to the total for that side as audio (the
    # dominant, most expensive modality) so the estimate stays conservative.
    if in_audio == 0 and in_text == 0:
        in_audio = _int(usage.get("input_tokens"))
    if out_audio == 0 and out_text == 0:
        out_audio = _int(usage.get("output_tokens"))

    fresh_audio_in = max(0, in_audio - cached_audio)
    cost = (
        fresh_audio_in * (RT_AUDIO_IN + TRANSCRIBE_AUDIO_IN)
        + cached_audio * RT_AUDIO_IN_CACHED
        + in_text * RT_TEXT_IN
        + out_audio * RT_AUDIO_OUT
        + out_text * RT_TEXT_OUT
    ) / PER_MILLION
    return max(0.0, cost)
