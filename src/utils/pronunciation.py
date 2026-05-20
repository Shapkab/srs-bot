"""Generate IPA pronunciation via OpenAI (gpt-4o-mini)."""

from __future__ import annotations

import logging
import time

from openai import OpenAI

log = logging.getLogger(__name__)

PROMPT = (
    "You are a pronunciation expert. Given an English word or phrase, "
    "return ONLY its IPA transcription.\n"
    "- Use American English pronunciation\n"
    "- Return ONLY the IPA symbols inside slashes, nothing else\n"
    "- For phrases, separate words with spaces\n"
    '- Example input: "hello" -> Example output: /həˈloʊ/\n'
    '- Example input: "reach out to" -> Example output: /riːtʃ aʊt tuː/\n'
    "Word/phrase: {text}"
)

# Base delay for exponential backoff between retries (seconds): the
# Nth retry waits BACKOFF_BASE * 2**N. Kept small — these are
# user-facing /add calls, not a bulk job.
BACKOFF_BASE = 1.0


def _normalize(ipa: str) -> str:
    """Ensure the transcription is wrapped in slashes."""
    ipa = ipa.strip()
    if not ipa.startswith("/"):
        ipa = "/" + ipa
    if not ipa.endswith("/"):
        ipa = ipa + "/"
    return ipa


def generate_pronunciation(text: str, api_key: str, max_retries: int = 3) -> str:
    """Return the IPA pronunciation for ``text``.

    Retries up to ``max_retries`` times with exponential backoff
    (BACKOFF_BASE * 2**attempt seconds). Raises the last exception if
    every attempt fails — callers are expected to surface a
    user-facing error and abort whatever they were doing.
    """
    client = OpenAI(api_key=api_key)

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": PROMPT.format(text=text)}],
                max_tokens=100,
                temperature=0.1,
            )
            ipa = _normalize(response.choices[0].message.content or "")
            log.info(
                "generated pronunciation",
                extra={"event": "pronunciation_generated"},
            )
            return ipa
        except Exception as e:
            log.warning("OpenAI attempt %d/%d failed: %s", attempt + 1, max_retries, e)
            if attempt == max_retries - 1:
                raise
            time.sleep(BACKOFF_BASE * (2 ** attempt))

    # Unreachable — the final attempt either returns or re-raises above.
    raise RuntimeError("Failed to generate pronunciation")
