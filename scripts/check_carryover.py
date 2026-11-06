#!/usr/bin/env python3
"""
check_carryover.py - verify a carry-over packet against the conversation it came from.

Part of the carry-over skill. Standard library only.

What it checks
  1. Structure: required sections present and non-empty, exactly one PICK UP HERE line.
     (A short note, marked "format: short" in the header, needs only five sections.)
  2. Tags: every item is tagged [V] verified, [I] inferred, or [U] unknown, and every [V]
     item carries at least one (src: "quote").
  3. Quotes: each quote really appears in the transcript (exact, or a flagged near match).
  4. Hygiene: leftover placeholders, secrets, header counts that don't match, and size.

Usage
    python3 check_carryover.py packet.md --transcript chat.txt
    python3 check_carryover.py packet.md --transcript session.jsonl --json
    python3 check_carryover.py packet.md --strict          # exit 1 on any error
    python3 check_carryover.py --find                       # list likely transcript files

Quotes are checked against everything in the transcript that came BEFORE the packet's own
id line, so a packet cannot "prove" itself by quoting itself.

A pass means the quotes exist. It does not prove the packet is complete or that a
quote supports the claim next to it: a person (or the next session) still has to read it.
"""

import argparse
import difflib
import glob
import json
import os
import re
import sys
import time

ERROR, WARNING, NOTE = "error", "warning", "note"
findings = []

REQUIRED_SECTIONS = [
    "Goal", "PICK UP HERE", "Current state", "Decisions", "Dead ends",
    "Files and artifacts", "Preferences and constraints", "Open questions", "Next steps",
]
# A short note (header contains "format: short") needs only these.
SHORT_SECTIONS = ["Goal", "PICK UP HERE", "Decisions", "Open questions", "Next steps"]
SECRET_PATTERNS = [
    (r"sk-[A-Za-z0-9_\-]{20,}", "API key (sk-...)"),
    (r"AKIA[0-9A-Z]{16}", "AWS access key id"),
    (r"gh[pousr]_[A-Za-z0-9]{30,}", "GitHub token"),
    (r"xox[abprs]-[A-Za-z0-9\-]{10,}", "Slack token"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key block"),
    (r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}", "JWT"),
    (r"(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}", "bearer token"),
    (r"(?i)\b(password|passwd|secret|api[_-]?key|token)\s*[:=]\s*[\"']?[^\s\"']{6,}", "credential assignment"),
]
PLACEHOLDER_RE = re.compile(r"(?i)\b(TODO|TBD|FIXME|REPLACE|lorem ipsum)\b|<[^<>\n]{2,40}>|\[\.\.\.\]|\bXXX\b")
TAG_RE = re.compile(r"^\s*(?:[-*]|\d+[.)])\s*\[([VIU])\]\s*(.*)$")
SRC_RE = re.compile(r"\(src:\s*((?:\"[^\"]+\"\s*\|?\s*)+)\)")
QUOTE_RE = re.compile(r"\"([^\"]+)\"")
ID_RE = re.compile(r"(?im)^\s*id:\s*([A-Za-z0-9\-_]{6,40})\b|\bid:\s*([A-Za-z0-9\-_]{6,40})\b")


def add(sev, rule, msg, line=None, snippet=None):
    findings.append({"severity": sev, "rule": rule, "message": msg, "line": line,
                     "snippet": (snippet or "").strip()[:120] or None})


# ---------------------------------------------------------------- transcript loading
def collect_strings(obj, out):
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, list):
        for v in obj:
            collect_strings(v, out)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("signature", "id", "uuid", "sessionId", "parentUuid", "timestamp", "type", "role", "model"):
                continue
            collect_strings(v, out)


def load_transcript(path):
    raw = open(path, "r", encoding="utf-8", errors="replace").read()
    low = path.lower()
    if low.endswith(".jsonl"):
        parts = []
        for ln in raw.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                collect_strings(json.loads(ln), parts)
            except json.JSONDecodeError:
                parts.append(ln)
        return "\n".join(parts)
    if low.endswith(".json"):
        try:
            parts = []
            collect_strings(json.loads(raw), parts)
            return "\n".join(parts)
        except json.JSONDecodeError:
            return raw
    return raw


def norm(text):
    t = text.lower()
    t = (t.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
          .replace("\u2013", "-").replace("\u2014", "-").replace("\u2026", "..."))
    t = re.sub(r"[*`]", "", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def verify_quote(q, hay):
    """Return ('exact'|'near'|'missing', detail)."""
    nq = norm(q)
    if not nq:
        return "missing", "empty quote"
    if nq in hay:
        return "exact", ""
    words = nq.split(" ")
    anchor = " ".join(words[: min(3, len(words))])
    best, start, tries = 0.0, 0, 0
    while tries < 300:
        start = hay.find(anchor, start)
        if start < 0:
            break
        window = hay[start: start + len(nq) + 20]
        r = difflib.SequenceMatcher(None, nq, window[: len(nq)]).ratio()
        best = max(best, r)
        start += 1
        tries += 1
    if best >= 0.9:
        return "near", f"closest match {best:.0%}"
    return "missing", f"closest match {best:.0%}" if best else "no similar text"


# ---------------------------------------------------------------- packet parsing
def parse_packet(text):
    lines = text.splitlines()
    sections, cur, buf = {}, None, []
    for i, ln in enumerate(lines, 1):
        m = re.match(r"^\s*#{2,3}\s+(.+?)\s*$", ln)
        if m:
            if cur is not None:
                sections[cur] = buf
            cur, buf = m.group(1).strip(), []
        elif cur is not None:
            buf.append((i, ln))
    if cur is not None:
        sections[cur] = buf
    return lines, sections


def find_section(sections, name):
    for k in sections:
        if k.lower().startswith(name.lower()):
            return k
    return None


def check_packet(path, transcript_path, force_short=False):
    text = open(path, "r", encoding="utf-8", errors="replace").read()
    lines, sections = parse_packet(text)

    if not re.search(r"(?m)^\s*CARRY-OVER PACKET\b", text):
        add(ERROR, "no-header", "Missing the 'CARRY-OVER PACKET v1' header line.")
    m = ID_RE.search(text)
    packet_id = (m.group(1) or m.group(2)) if m else None
    if not packet_id:
        add(WARNING, "no-id", "No 'id:' in the header. The id lets this checker ignore the packet's own text in the transcript.")

    # structure
    is_short = force_short or bool(re.search(r"(?i)\bformat:\s*short\b", text[:600]))
    for req in (SHORT_SECTIONS if is_short else REQUIRED_SECTIONS):
        key = find_section(sections, req)
        if not key:
            add(ERROR, "missing-section", f"Missing required section: {req}.")
            continue
        body = [ln.strip() for _, ln in sections[key] if ln.strip()]
        if not body:
            add(ERROR, "empty-section", f"Section '{req}' is empty. Write 'None.' if there is truly nothing.")
    pk = find_section(sections, "PICK UP HERE")
    if pk:
        body = [ln.strip() for _, ln in sections[pk] if ln.strip()]
        if len(body) != 1:
            add(ERROR, "pick-up-here", f"PICK UP HERE must be exactly one line (found {len(body)}).")

    # transcript haystack
    hay = None
    if transcript_path:
        raw = load_transcript(transcript_path)
        if packet_id:
            idx = raw.find(packet_id)
            if idx > 0:
                raw = raw[:idx]
        hay = norm(raw)
        if len(hay) < 200:
            add(WARNING, "tiny-transcript", "Transcript is very short; quote checks may be meaningless.")

    # items
    counts = {"V": 0, "I": 0, "U": 0}
    checked = {"exact": 0, "near": 0, "missing": 0}
    for i, ln in enumerate(lines, 1):
        m = TAG_RE.match(ln)
        if not m:
            continue
        tag, rest = m.group(1), m.group(2)
        counts[tag] += 1
        sm = SRC_RE.search(rest)
        quotes = QUOTE_RE.findall(sm.group(1)) if sm else []
        if tag == "V" and not quotes:
            add(ERROR, "verified-no-quote", "A [V] item has no (src: \"quote\"). Add a quote or downgrade it to [I].", i, ln)
        for q in quotes:
            n = len(q.split())
            if n < 3:
                add(WARNING, "short-quote", f"Quote is only {n} word(s); too generic to prove anything.", i, q)
            elif n > 25:
                add(WARNING, "long-quote", f"Quote is {n} words; keep quotes short (3 to 15 words).", i, q)
            if hay is not None:
                status, detail = verify_quote(q, hay)
                checked[status] += 1
                if status == "missing":
                    add(ERROR, "quote-not-found",
                        f"Quote not found in the transcript ({detail}). Fix the quote or downgrade the item to [I].", i, q)
                elif status == "near":
                    add(WARNING, "quote-near-match",
                        f"Quote is not verbatim ({detail}). Copy the exact words.", i, q)
    if sum(counts.values()) == 0:
        add(ERROR, "no-items", "No tagged items found. Items look like: '- [V] text (src: \"quote\")'.")

    # header counts
    hm = re.search(r"verified:\s*(\d+).*?inferred:\s*(\d+).*?unknown:\s*(\d+)", text, re.I | re.S)
    if hm:
        got = tuple(int(x) for x in hm.groups())
        want = (counts["V"], counts["I"], counts["U"])
        if got != want:
            add(WARNING, "header-counts", f"Header says verified/inferred/unknown = {got}, items say {want}.")
    else:
        add(NOTE, "no-header-counts", "Header has no 'verified: N inferred: N unknown: N' counts.")

    # hygiene
    for pat, label in SECRET_PATTERNS:
        for mm in re.finditer(pat, text):
            ln = text.count("\n", 0, mm.start()) + 1
            add(ERROR, "secret", f"Possible secret in the packet ({label}). Replace it with [REDACTED].", ln, mm.group(0)[:30] + "...")
            break
    for mm in PLACEHOLDER_RE.finditer(text):
        ln = text.count("\n", 0, mm.start()) + 1
        line_txt = lines[ln - 1] if ln - 1 < len(lines) else ""
        if "src:" in line_txt:
            continue
        add(WARNING, "placeholder", "Leftover placeholder text.", ln, mm.group(0))
        break
    words = len(re.findall(r"\w+", text))
    if words > 6000:
        add(ERROR, "too-long", f"{words} words is too long to carry; trim, or move detail into a file.")
    elif words > 2500:
        add(WARNING, "long", f"{words} words. Packets above about 2,500 words are hard for the next session to use; prioritize.")

    return counts, checked, words, hay is not None


# ---------------------------------------------------------------- transcript discovery
def find_transcripts():
    patterns = [
        "/mnt/transcripts/*", "/mnt/user-data/uploads/*.jsonl", "/mnt/user-data/uploads/*.txt",
        os.path.expanduser("~/.claude/projects/*/*.jsonl"),
        os.path.expanduser("~/.codex/sessions/**/*.jsonl"),
        "./*.jsonl", "./transcript*", "./chat*.txt", "./chat*.md",
    ]
    rows = []
    for p in patterns:
        for f in glob.glob(p, recursive=True):
            if os.path.isfile(f):
                st = os.stat(f)
                rows.append((st.st_mtime, st.st_size, f))
    rows.sort(reverse=True)
    return rows[:15]


def main():
    ap = argparse.ArgumentParser(description="Verify a carry-over packet against its transcript.")
    ap.add_argument("packet", nargs="?", help="the packet or HANDOFF.md file")
    ap.add_argument("--transcript", help="the conversation: .txt, .md, .json, or .jsonl")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true", help="exit 1 if any error is found")
    ap.add_argument("--short", action="store_true", help="treat the packet as a short note (fewer required sections)")
    ap.add_argument("--find", action="store_true", help="list likely transcript files and exit")
    a = ap.parse_args()

    if a.find:
        rows = find_transcripts()
        if not rows:
            print("No transcript files found in the usual places. Ask the user to export or paste the conversation.")
        for mt, size, f in rows:
            print(f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(mt))}  {size / 1024:8.1f} KB  {f}")
        return
    if not a.packet:
        ap.error("packet file is required (or use --find)")
    for p in (a.packet, a.transcript):
        if p and not os.path.isfile(p):
            print(f"error: file not found: {p}", file=sys.stderr)
            sys.exit(2)

    counts, checked, words, had_t = check_packet(a.packet, a.transcript, a.short)
    order = {ERROR: 0, WARNING: 1, NOTE: 2}
    findings.sort(key=lambda f: (order[f["severity"]], f["line"] or 10**9))
    sc = {s: sum(1 for f in findings if f["severity"] == s) for s in (ERROR, WARNING, NOTE)}
    summary = {"verified": counts["V"], "inferred": counts["I"], "unknown": counts["U"],
               "words": words, "quotes_checked": sum(checked.values()) if had_t else None,
               "quotes_exact": checked["exact"] if had_t else None,
               "quotes_near": checked["near"] if had_t else None,
               "quotes_missing": checked["missing"] if had_t else None}
    if a.json:
        print(json.dumps({"packet": a.packet, "transcript": a.transcript, "counts": sc,
                          "summary": summary, "findings": findings}, indent=2))
    else:
        print(f"check_carryover: {a.packet}")
        print(f"  items: {counts['V']} verified, {counts['I']} inferred, {counts['U']} unknown | {words} words")
        if had_t:
            print(f"  quotes: {checked['exact']} exact, {checked['near']} near, {checked['missing']} not found")
        else:
            print("  quotes: NOT CHECKED (no --transcript given)")
        print(f"  {sc[ERROR]} error(s), {sc[WARNING]} warning(s), {sc[NOTE]} note(s)\n")
        tag = {ERROR: "ERROR  ", WARNING: "WARNING", NOTE: "NOTE   "}
        for f in findings:
            where = f"line {f['line']}" if f["line"] else "packet"
            print(f"[{tag[f['severity']]}] {f['rule']} ({where}): {f['message']}")
            if f["snippet"]:
                print(f"           > {f['snippet']}")
        if not findings:
            print("No findings. The quotes exist; a person still has to confirm they support each claim.")
    sys.exit(1 if (a.strict and sc[ERROR]) else 0)


if __name__ == "__main__":
    main()
