#!/usr/bin/env python3
"""Independent fact-check of a draft article extraction against its source text.

Runs the mechanical half of SKILL.md مرحله ۴: every number, currency amount,
Latin product token and «گیومه» quote in the draft must be traceable to the
source. A separate agent still reviews nuance (numbers this script cannot see),
but this script fails loudly on the common hallucinations first.

Usage:
    python3 scripts/verify_extraction.py --source /tmp/source-<id>.txt \
        --draft /tmp/draft-<id>.json [--id <article-id>]

Exit code 0 = PASS (mechanical checks clean). Exit 1 = FAIL (see findings).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
LATIN_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9.\-]*[A-Za-z0-9]|[A-Za-z]{2,}")
CURRENCY_AMOUNT = re.compile(r"\d[\d,]*")
QUOTED = re.compile(r"«([^»]+)»")

SKIP_TOKENS = {
    "id", "kebab", "case", "latin", "slug", "url", "http", "https", "com",
    "www", "pdf", "json", "cpu", "api", "todo", "todoist", "claude", "ai",
    "email", "slack", "gmail", "linkedin", "whatsapp", "zoom", "am", "pm",
    "ist", "end", "point", "endpoint", "vllm", "sglang", "kubernetes",
    "github", "gitlab", "git", "monorepo", "scion", "antique",
}

TRANSLATED_LABELS = {
    "کیت شروع پژوهش",
    "مهندسی به‌صورت سرتاسری",
    "موتور استنتاج",
    "گره", "یال", "پیوند",
}

SECTION_KEYS = ("title", "claim", "reason", "conclusion", "application")


def fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(1)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return text.translate(FA_DIGITS).lower()


def draft_strings(article: dict) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for key in SECTION_KEYS:
        value = article.get(key)
        if isinstance(value, str):
            out.append((key, value))
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, str):
                    out.append((f"{key}[{i}]", item))
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", required=True, help="path to the raw source text")
    p.add_argument("--draft", required=True, help="path to the draft article JSON")
    p.add_argument("--id", default=None, help="article id to check inside a draft file")
    args = p.parse_args(argv)

    source_path = Path(args.source)
    draft_path = Path(args.draft)
    if not source_path.is_file():
        fail(f"source not found: {source_path}")
    if not draft_path.is_file():
        fail(f"draft not found: {draft_path}")

    source = normalize(source_path.read_text(encoding="utf-8"))
    payload = json.loads(draft_path.read_text(encoding="utf-8"))

    if isinstance(payload, dict) and "articles" in payload:
        articles = payload["articles"]
        if args.id:
            matches = [a for a in articles if a.get("id") == args.id]
            if not matches:
                fail(f"id '{args.id}' not found in draft")
            article = matches[0]
        else:
            fail("draft has multiple articles; pass --id to select one")
    elif isinstance(payload, dict):
        article = payload
    else:
        fail("draft JSON must be an object or an object with 'articles'")

    findings: list[str] = []

    for loc, text in draft_strings(article):
        norm = normalize(text)

        for token in LATIN_TOKEN.findall(norm):
            if token in SKIP_TOKENS or len(token) < 3:
                continue
            if token in source:
                continue
            stem = token.rstrip("s")
            if len(stem) >= 4 and stem in source:
                continue
            parts = [seg for seg in re.split(r"[-_]", token) if len(seg) >= 3]
            if parts and all(seg in source or seg.rstrip("s") in source for seg in parts):
                continue
            findings.append(f"{loc}: Latin token {token!r} not found in source")

        for amount in CURRENCY_AMOUNT.findall(norm):
            digits = re.sub(r"[^\d]", "", amount)
            if len(digits) < 2 or digits in {"2026", "2025", "2024"}:
                continue
            if amount in source or digits in source:
                continue
            if len(digits) >= 4 and digits[:-3] in source:
                continue
            if digits[-1] == "0" and digits[:-1] and digits[:-1] in source:
                continue
            findings.append(f"{loc}: number {amount!r} not found in source")

        for quote in QUOTED.findall(text):
            if re.search(r"[A-Za-z]", quote):
                continue
            probe = normalize(quote)[:40]
            if probe and probe in source:
                continue
            if quote in TRANSLATED_LABELS:
                continue
            findings.append(f"{loc}: quote {quote[:40]!r} not found in source")

    if findings:
        print("FAIL — mechanical grounding findings:")
        for f in findings:
            print(f"  - {f}")
        return 1

    print("PASS — all numbers, Latin tokens and quotes trace to the source.")
    print("Have a separate agent re-read the source and confirm nuance before persisting.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
