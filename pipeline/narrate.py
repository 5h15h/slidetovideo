#!/usr/bin/env python3
"""Turn speaker notes into spoken narration.

Two paths:
  compose_basic  - deterministic, no key, no network. Competent, not literary.
  compose_llm    - Anthropic API, applies the full craft rules. Eloquent.

Plus `audit`, which flags the things that make narration sound wrong when
read by a machine: unspoken digits, surviving stage directions, recited bullets.
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import split_sentences

# --- numbers to words --------------------------------------------------------
_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
         "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
         "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
         "eighty", "ninety"]
_SCALE = [(1_000_000_000, "billion"), (1_000_000, "million"), (1_000, "thousand")]


def int_to_words(n: int) -> str:
    if n < 0:
        return "minus " + int_to_words(-n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        t, r = divmod(n, 10)
        return _TENS[t] + ("-" + _ONES[r] if r else "")
    if n < 1000:
        h, r = divmod(n, 100)
        return _ONES[h] + " hundred" + (" and " + int_to_words(r) if r else "")
    for value, name in _SCALE:
        if n >= value:
            q, r = divmod(n, value)
            return int_to_words(q) + " " + name + (" " + int_to_words(r) if r else "")
    return str(n)


def year_to_words(n: int) -> str:
    """1996 -> nineteen ninety-six; 2026 -> twenty twenty-six; 2000 -> two thousand."""
    if 1100 <= n <= 1999 or 2010 <= n <= 2099:
        hi, lo = divmod(n, 100)
        if lo == 0:
            return int_to_words(hi) + " hundred"
        return int_to_words(hi) + " " + ("oh " + _ONES[lo] if lo < 10 else int_to_words(lo))
    if 2000 <= n <= 2009:
        return "two thousand" + (" " + _ONES[n - 2000] if n > 2000 else "")
    return int_to_words(n)


def decimal_to_words(s: str) -> str:
    if "." not in s:
        return int_to_words(int(s))
    whole, frac = s.split(".", 1)
    w = int_to_words(int(whole)) if whole else "zero"
    return w + " point " + " ".join(_ONES[int(d)] for d in frac if d.isdigit())


_MAG = {"k": "thousand", "m": "million", "b": "billion", "bn": "billion", "t": "trillion"}
_CUR = {"$": ("dollar", "dollars"), "£": ("pound", "pounds"), "€": ("euro", "euros")}

_ABBR = [
    (r"\be\.g\.", "for example"), (r"\bi\.e\.", "that is"),
    (r"\betc\.", "and so on"), (r"\bvs\.?\b", "versus"),
    (r"\bYoY\b", "year over year"), (r"\bQoQ\b", "quarter over quarter"),
    (r"\bMoM\b", "month over month"), (r"\bFY\s*(\d{2,4})\b", r"fiscal year \1"), (r"\bFY\b", "fiscal year"),
    (r"\bASAP\b", "as soon as possible"), (r"\bw/\b", "with"),
    (r"\bapprox\.", "approximately"), (r"\bNo\.\s*(\d)", r"number \1"),
    (r"&", " and "), (r"\bpts\b", "points"), (r"\bpt\b", "point"),
    (r"\bbps\b", "basis points"), (r"\bx(\d+(?:\.\d+)?)\b", r"\1 times"),
    (r"(\d)\s*[-–]\s*(\d)", r"\1 to \2"),
]


def speak_numbers(text: str) -> str:
    """Rewrite anything a synthesiser would read awkwardly into words."""
    t = text
    for pat, rep in _ABBR:
        t = re.sub(pat, rep, t)

    t = re.sub(r"\s+[-–]\s+", " \u2014 ", t)
    # Q3 -> Q three  (keep the letter, the room says "Q three")
    t = re.sub(r"\bQ([1-4])\b", lambda m: "Q " + _ONES[int(m.group(1))], t)
    # Day-7 / day 7 -> day seven
    t = re.sub(r"\b([Dd]ay|[Ww]eek|[Mm]onth|[Yy]ear|[Tt]ier|[Ss]tep|[Pp]hase)[-\s]+(\d{1,3})\b",
               lambda m: f"{m.group(1)} {int_to_words(int(m.group(2)))}", t)
    # currency with magnitude: $1.2M
    def _cur(m):
        sym, num, mag = m.group(1), m.group(2), (m.group(3) or "").lower()
        words = decimal_to_words(num)
        unit = _CUR.get(sym, ("dollar", "dollars"))[1]
        return f"{words} {_MAG[mag]} {unit}" if mag else f"{words} {unit}"
    t = re.sub(r"([$£€])\s?(\d+(?:\.\d+)?)\s*([kKmMbB][nN]?|[tT])?", _cur, t)
    # percentages
    t = re.sub(r"(\d+(?:\.\d+)?)\s*%", lambda m: decimal_to_words(m.group(1)) + " percent", t)
    # bare magnitudes: 40k
    t = re.sub(r"\b(\d+(?:\.\d+)?)\s*([kKmMbB][nN]?)\b",
               lambda m: f"{decimal_to_words(m.group(1))} {_MAG[m.group(2).lower()]}", t)
    # ordinals
    _ORD = {"1": "first", "2": "second", "3": "third", "4": "fourth", "5": "fifth",
            "6": "sixth", "7": "seventh", "8": "eighth", "9": "ninth", "10": "tenth"}
    t = re.sub(r"\b(\d{1,2})(st|nd|rd|th)\b",
               lambda m: _ORD.get(m.group(1), int_to_words(int(m.group(1))) + "th"), t)
    # years
    t = re.sub(r"\b(1[1-9]\d{2}|20\d{2})\b", lambda m: year_to_words(int(m.group(1))), t)
    # everything else numeric
    t = re.sub(r"\b\d+(?:\.\d+)?\b", lambda m: decimal_to_words(m.group(0)), t)
    t = re.sub(r"\s{2,}", " ", t)
    return t.strip()


# --- stage directions --------------------------------------------------------
# Pure delivery cues: nothing to say, drop the whole sentence.
_DIRECTION_PURE = re.compile(
    r"^\s*(pause|stop|slow down|speed up|breathe|smile|look up|make eye contact|"
    r"be (?:direct|brief|quick|warm|careful)|keep it (?:tight|short|brief|light)|"
    r"don'?t (?:oversell|rush|linger|read)|do not (?:oversell|rush)|"
    r"say (?:that|this|it) (?:out loud|slowly|clearly)|read (?:this|that) out|"
    r"skip (?:if|this)|cut (?:if|this)|optional|if (?:short on )?time|"
    r"ask the room|take questions|hand (?:over|off)|transition|segue|"
    r"let it land|no notes|timebox|\d+\s*(?:min|minute|sec|second)s?\b)\b", re.I)

# Content-bearing prefixes: the instruction is to the presenter, the clause
# after it is real material. Strip the prefix, keep the clause.
_DIRECTION_PREFIX = re.compile(
    r"^\s*(?:"
    r"(?:note|remind|call out|flag|emphasi[sz]e|stress|mention|point out|"
    r"highlight)\s*(?:that|the|this|them|the room|again)?"
    r"|(?:open|start|lead|close|end|finish|wrap)\s+(?:with|on|up with)"
    r"|frame\s+(?:the talk|it|this)"
    r")[\s,:–—-]+", re.I)

_DIRECTION = re.compile(
    _DIRECTION_PURE.pattern + "|" + _DIRECTION_PREFIX.pattern, re.I)

_DIR_INLINE = re.compile(
    r"\b(pause here|let it land|keep it tight|be direct|say that out loud|"
    r"don'?t oversell|no notes|slow down here|smile)\b[\s,.;:—-]*", re.I)


def _cap(s: str) -> str:
    s = _DIR_INLINE.sub("", s).strip(" ,;:-\u2014")
    if not s:
        return ""
    s = s[0].upper() + s[1:]
    # Removing a trailing cue can leave a sentence without its full stop,
    # which would run it into the next one.
    return s if s[-1] in ".!?" else s + "."


def _strip_direction(sentence: str) -> str:
    """Drop a pure stage direction; keep the substance of a mixed one."""
    s = sentence.strip()
    if not s:
        return ""
    if _DIRECTION_PURE.match(s):
        # "Pause here: the tickets number is the point" -> keep the clause
        m = re.search(r"[:–—]\s*(.+)$", s)
        if m and len(m.group(1).split()) >= 4:
            return _cap(m.group(1))
        return ""
    m = _DIRECTION_PREFIX.match(s)
    if m:
        rest = s[m.end():]
        # "Note the tickets number: people are trying" -> prefer the clause
        after = re.search(r"[:–—]\s*(.+)$", rest)
        if after and len(after.group(1).split()) >= 3:
            return _cap(after.group(1))
        return _cap(rest) if len(rest.split()) >= 3 else ""
    cleaned = _DIR_INLINE.sub("", s).strip(" ,;:-\u2014")
    if len(cleaned.split()) < 3:
        return ""
    return _cap(cleaned)


TRANSITIONS = [
    "", "Which brings us to the next piece.", "So what does that actually mean?",
    "Here's where it gets interesting.", "Now, the part that matters most.",
    "With that in mind.", "That leads somewhere useful.",
    "So let's look at what follows from that.",
]


def compose_basic(deck: dict, add_transitions: bool = True) -> dict:
    """Deterministic draft. Honest about its ceiling: it tidies notes for the
    mouth, it does not write prose."""
    slides = []
    for i, s in enumerate(deck.get("slides", [])):
        notes = (s.get("notes") or "").strip()
        if s.get("hidden") or not notes:
            slides.append({"n": s["n"], "skip": True, "narration": ""})
            continue
        kept = [c for c in (_strip_direction(x) for x in split_sentences(notes)) if c]
        if not kept:
            slides.append({"n": s["n"], "skip": True, "narration": ""})
            continue
        kept = [_cap(speak_numbers(c)) for c in kept]
        if add_transitions and i > 0 and len(kept) > 1:
            lead = TRANSITIONS[i % len(TRANSITIONS)]
            if lead and not re.match(r"^(so|now|which|here|and|but|that)\b", kept[0], re.I):
                kept.insert(0, lead)
        paras, buf = [], []
        for c in kept:
            buf.append(c)
            if len(buf) >= 3:
                paras.append(" ".join(buf)); buf = []
        if buf:
            paras.append(" ".join(buf))
        slides.append({"n": s["n"], "skip": False, "narration": "\n\n".join(paras)})
    return {
        "deck_title": deck.get("deck_title", "Presentation"),
        "voice": None, "intro": None,
        "outro": "Thanks for listening.", "outro_title": "Thank you",
        "slides": slides,
    }


# --- LLM path ----------------------------------------------------------------
SYSTEM = """You write voiceover narration for slide decks. You are given each \
slide's title, visible text and the presenter's own speaker notes. You return \
narration to be read aloud.

Rules, in order of importance:

1. FAITHFUL. Speaker notes are the source of truth for facts. Expand rhetoric, \
never substance. Never introduce a number, name, date, claim or caveat that is \
not in the notes or on the slide. If the notes are thin, write less.

2. STAGE DIRECTIONS ARE FOR YOU, NOT THE MICROPHONE. Notes contain instructions \
to the presenter: "pause here", "let it land", "keep it tight, 12 min", "be \
direct", "say that out loud", "don't oversell". Never speak them. Obey them: a \
pause becomes a paragraph break; "be direct" becomes short sentences; "keep it \
tight" means fewer words.

3. SPOKEN, NOT WRITTEN. Contractions. One idea per sentence. No parentheses, \
semicolons, bullet syntax or "etc." Never say "as you can see on this slide" or \
"moving on to the next slide". Never recite the bullets - the viewer is already \
reading them. Say what the bullets leave out: why they are true, what they cost, \
what to do about them.

4. SAY NUMBERS THE WAY A MOUTH SAYS THEM. "68%" -> "sixty-eight percent". \
"$1.2M" -> "one point two million dollars". "Q3" -> "Q three". "+6 pts" -> "up \
six points". "Day-7" -> "day seven". No digits, %, $ or & may appear in output.

5. BUILD AN ARC. Open the deck with a hook from the notes, not "Hello and \
welcome". Open each later slide by connecting it to the one before, varying the \
connective. Close by landing the opening idea again if the notes support it.

6. LENGTH. Match the notes' weight. A sparse slide gets two or three sentences; \
a dense one gets up to four short paragraphs. Separate paragraphs with a blank \
line - each is a real pause in delivery.

Return ONLY JSON: {"slides":[{"n":1,"narration":"..."}, ...], \
"outro":"short closing line"}. Use \\n\\n between paragraphs. Omit any slide \
that should be skipped."""


def compose_llm(deck: dict, api_key: str, model: str = "claude-opus-4-5",
                tone: str = "", timeout: int = 240) -> dict:
    import requests
    payload_slides = []
    for s in deck.get("slides", []):
        if s.get("hidden"):
            continue
        payload_slides.append({"n": s["n"], "title": s.get("title", ""),
                               "slide_text": s.get("body", []),
                               "speaker_notes": s.get("notes", "")})
    if not any(x["speaker_notes"].strip() for x in payload_slides):
        raise RuntimeError("This deck has no speaker notes to expand.")

    user = (f"Deck title: {deck.get('deck_title','')}\n"
            + (f"Requested tone: {tone}\n" if tone.strip() else "")
            + f"\nSlides:\n{json.dumps(payload_slides, indent=1, ensure_ascii=False)}")
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": api_key.strip(), "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": model, "max_tokens": 8000, "system": SYSTEM,
              "messages": [{"role": "user", "content": user}]},
        timeout=timeout)
    if r.status_code != 200:
        detail = ""
        try:
            detail = r.json().get("error", {}).get("message", "")
        except Exception:
            detail = r.text[:300]
        if r.status_code == 401:
            raise RuntimeError("Anthropic rejected the API key (401).")
        raise RuntimeError(f"Anthropic error {r.status_code}. {detail}")

    text = "".join(b.get("text", "") for b in r.json().get("content", []))
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise RuntimeError("The model did not return JSON.")
    data = json.loads(m.group(0))

    written = {int(s["n"]): (s.get("narration") or "").strip()
               for s in data.get("slides", []) if s.get("n") is not None}
    slides = []
    for s in deck.get("slides", []):
        txt = written.get(s["n"], "")
        slides.append({"n": s["n"], "skip": not txt, "narration": txt})
    return {
        "deck_title": deck.get("deck_title", "Presentation"),
        "voice": None, "intro": None,
        "outro": (data.get("outro") or "Thanks for listening.").strip(),
        "outro_title": "Thank you", "slides": slides,
    }


# --- audit -------------------------------------------------------------------
def audit(narration: dict, deck: dict | None = None) -> list[dict]:
    """Flag what makes narration sound wrong out loud."""
    out: list[dict] = []
    bodies = {}
    if deck:
        bodies = {s["n"]: [b.lower().strip() for b in (s.get("body") or [])]
                  for s in deck.get("slides", [])}
    for s in narration.get("slides", []):
        n, text = s.get("n"), (s.get("narration") or "")
        if s.get("skip") or not text.strip():
            continue
        bad = re.findall(r"\d+|[%$&]", text)
        if bad:
            out.append({"n": n, "level": "warn", "kind": "unspoken",
                        "message": "Contains characters a voice will read "
                                   f"awkwardly: {', '.join(sorted(set(bad))[:6])}"})
        for sent in split_sentences(text):
            if _DIRECTION.match(sent) or _DIR_INLINE.search(sent):
                out.append({"n": n, "level": "warn", "kind": "direction",
                            "message": f"Looks like a note to the presenter: "
                                       f"“{sent[:70]}”"})
                break
        low = text.lower()
        for phrase in ("as you can see", "on this slide", "next slide",
                       "this slide shows"):
            if phrase in low:
                out.append({"n": n, "level": "warn", "kind": "deictic",
                            "message": f"Says “{phrase}” - the slide "
                                       "is already visible"})
                break
        recited = sum(1 for b in bodies.get(n, []) if b and len(b) > 12 and b in low)
        if recited >= 2:
            out.append({"n": n, "level": "info", "kind": "recital",
                        "message": f"Repeats {recited} bullets close to verbatim"})
        words = len(text.split())
        if words > 230:
            out.append({"n": n, "level": "info", "kind": "long",
                        "message": f"{words} words - about "
                                   f"{words/150*60:.0f}s on one slide"})
    return out
