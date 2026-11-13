"""Domain vocabulary for Whisper phrase boosting (hotwords + post-decode fixes).

Private PhoLog terms that Whisper mishears; inject into whisper-timestamped
via LiveConfig / process_recordings kwargs (not baked into the public fork).
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

DOMAIN_HOTWORDS: List[str] = ["EEG", "AMPH", "1.0+", "0.5+", "log"]

DOMAIN_INITIAL_PROMPT: str = (
    "Lab notes covering EEG, AMPH dosing at 1.0+ or 0.5+, and log entries."
)

# Ordered (pattern, replacement). Longer / more specific phrases first.
# Bare "log" is intentionally not rewritten — only boosted via hotwords.
_DOMAIN_CORRECTION_SPECS: Sequence[Tuple[str, str]] = (
    # Spoken / spelled 1.0+ (allow Whisper spacing like "1 .0  plus")
    (r"\bone[\s-]+point[\s-]+(?:oh|o|zero)[\s-]+plus\b", "1.0+"),
    (r"\b1[\s-]*point[\s-]*(?:oh|o|zero)[\s-]*plus\b", "1.0+"),
    (r"\b1\s*\.\s*0\s*(?:plus|\+)\b", "1.0+"),
    (r"\b1\s*\.\s*0\+", "1.0+"),
    # Spoken / spelled 0.5+ (allow Whisper spacing like "0 .5  plus")
    (r"\b(?:zero|oh|o)[\s-]+point[\s-]+five[\s-]+plus\b", "0.5+"),
    (r"\b0[\s-]*point[\s-]*five[\s-]*plus\b", "0.5+"),
    (r"\b0\s*\.\s*5\s*(?:plus|\+)\b", "0.5+"),
    (r"\b0\s*\.\s*5\+", "0.5+"),
    # EEG (optional trailing period after spelled forms)
    (r"\bE[\s.-]*E[\s.-]*G\.?(?!\w)", "EEG"),
    (r"\beegee\b", "EEG"),
    (r"\beeg\b", "EEG"),
    # AMPH
    (r"\bA[\s.-]*M[\s.-]*P[\s.-]*H\.?(?!\w)", "AMPH"),
    (r"\bamph\b", "AMPH"),
    (r"\bamp\b", "AMPH"),
    (r"\bamf\b", "AMPH"),
)

DOMAIN_CORRECTIONS: List[Tuple[re.Pattern[str], str]] = [
    (re.compile(pat, re.IGNORECASE), repl) for pat, repl in _DOMAIN_CORRECTION_SPECS
]


def get_hotwords() -> List[str]:
    """Return a copy of the default domain hotword list."""
    return list(DOMAIN_HOTWORDS)


def get_initial_prompt() -> str:
    """Return the default domain initial prompt string."""
    return DOMAIN_INITIAL_PROMPT


def apply_domain_corrections(text: str) -> str:
    """Apply ordered domain phrase corrections to a plain text string."""
    if not text:
        return text
    out = text
    for pattern, replacement in DOMAIN_CORRECTIONS:
        out = pattern.sub(replacement, out)
    return out


def _word_text_key(word: Dict[str, Any]) -> Optional[str]:
    if "text" in word:
        return "text"
    if "word" in word:
        return "word"
    return None


def _collapse_multiword_corrections(words: List[Dict[str, Any]]) -> None:
    """Collapse multi-token spoken forms (e.g. 'one point oh plus') into one word.

    Mutates ``words`` in place. Single-token corrections are already applied
    per word; this only merges spans that match multi-token patterns.
    """
    if len(words) < 2:
        return

    multi_specs = [
        (pat, repl)
        for pat, repl in _DOMAIN_CORRECTION_SPECS
        if r"[\s-" in pat or r"\s" in pat or "[\s.-]" in pat
    ]
    if not multi_specs:
        return

    i = 0
    while i < len(words):
        collapsed = False
        # Try windows from longest to shortest (max 5 tokens)
        max_n = min(5, len(words) - i)
        for n in range(max_n, 1, -1):
            span = words[i : i + n]
            keys = [_word_text_key(w) for w in span]
            if any(k is None for k in keys):
                continue
            joined = " ".join(str(span[j][keys[j]]) for j in range(n))
            for pat, repl in multi_specs:
                pattern = re.compile(pat, re.IGNORECASE)
                if pattern.fullmatch(joined.strip()):
                    first_key = keys[0]
                    new_word = dict(span[0])
                    new_word[first_key] = repl
                    if "end" in span[-1]:
                        new_word["end"] = span[-1]["end"]
                    words[i : i + n] = [new_word]
                    collapsed = True
                    break
            if collapsed:
                break
        if not collapsed:
            i += 1


def apply_domain_corrections_to_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """Rewrite ``text`` / word fields in a whisper-timestamped result dict.

    Returns the same dict (mutated) for convenience.
    """
    if not isinstance(result, dict):
        return result

    if isinstance(result.get("text"), str):
        result["text"] = apply_domain_corrections(result["text"])

    for seg in result.get("segments") or []:
        if not isinstance(seg, dict):
            continue
        if isinstance(seg.get("text"), str):
            seg["text"] = apply_domain_corrections(seg["text"])
        words = seg.get("words")
        if not isinstance(words, list):
            continue
        for w in words:
            if not isinstance(w, dict):
                continue
            key = _word_text_key(w)
            if key is not None and isinstance(w.get(key), str):
                w[key] = apply_domain_corrections(w[key])
        _collapse_multiword_corrections(words)
        # Keep segment text aligned with words when present
        if words:
            parts = []
            for w in words:
                key = _word_text_key(w)
                if key is not None:
                    parts.append(str(w[key]))
            if parts:
                seg["text"] = " ".join(parts).strip()

    # Re-join top-level text from segments when available
    segments = result.get("segments")
    if isinstance(segments, list) and segments:
        joined = " ".join(
            str(s.get("text", "")).strip()
            for s in segments
            if isinstance(s, dict) and s.get("text")
        ).strip()
        if joined:
            result["text"] = joined

    return result


def apply_domain_corrections_to_emission(emission: Dict[str, Any]) -> Dict[str, Any]:
    """Correct a live emission dict (text + words) without breaking timestamps."""
    if not isinstance(emission, dict):
        return emission
    emission = copy.copy(emission)
    if isinstance(emission.get("text"), str):
        emission["text"] = apply_domain_corrections(emission["text"])
    words = emission.get("words")
    if isinstance(words, list):
        new_words = [dict(w) if isinstance(w, dict) else w for w in words]
        for w in new_words:
            if not isinstance(w, dict):
                continue
            key = _word_text_key(w)
            if key is not None and isinstance(w.get(key), str):
                w[key] = apply_domain_corrections(w[key])
        _collapse_multiword_corrections(new_words)
        emission["words"] = new_words
        if new_words:
            parts = []
            for w in new_words:
                if isinstance(w, dict):
                    key = _word_text_key(w)
                    if key is not None:
                        parts.append(str(w[key]))
            if parts:
                emission["text"] = " ".join(parts).strip()
    return emission
