#!/usr/bin/env python3
"""List, search and order articles in data/articles.json.

Usage:
    # Full list (default: newest registered first)
    python3 scripts/search_articles.py

    # Free-text search across title, concepts, claim/reason/conclusion,
    # application, source and id (case-insensitive)
    python3 scripts/search_articles.py --query "عامل"

    # Filter by one or more concepts (all must match)
    python3 scripts/search_articles.py --concept "عامل‌های هوش مصنوعی"

    # Order: registered_date | title | id  (default registered_date)
    python3 scripts/search_articles.py --sort title --asc
    python3 scripts/search_articles.py --sort registered_date --desc

    # Machine-readable output
    python3 scripts/search_articles.py --json

Exit code 0 always unless the data file is invalid.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT / "data" / "articles.json"

SORT_KEYS = ("registered_date", "title", "id")
SEARCH_FIELDS = (
    "id",
    "title",
    "source",
    "concepts",
    "claim",
    "reason",
    "conclusion",
    "application",
)


def flatten(article: dict) -> str:
    parts: list[str] = []
    for field in SEARCH_FIELDS:
        value = article.get(field)
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            parts.extend(str(item) for item in value)
    return "\n".join(parts)


def matches_query(article: dict, query: str) -> bool:
    haystack = flatten(article).lower()
    # Every whitespace-separated term must appear (AND semantics).
    return all(term.lower() in haystack for term in query.split())


def matches_concepts(article: dict, concepts: list[str]) -> bool:
    owned = set(article.get("concepts", []))
    return all(c in owned for c in concepts)


def sort_articles(articles: list[dict], key: str, descending: bool) -> list[dict]:
    if key == "title":
        # Persian-aware-ish: sort by code points, casefold for Latin.
        return sorted(articles, key=lambda a: a.get("title", "").casefold(), reverse=descending)
    # registered_date / id: plain string sort works for ISO dates and slugs.
    return sorted(articles, key=lambda a: a.get(key, ""), reverse=descending)


def print_rows(articles: list[dict]) -> None:
    if not articles:
        print("no matches")
        return
    date_w = max(len("registered_date"), *(len(a.get("registered_date", "")) for a in articles))
    for a in articles:
        date = a.get("registered_date", "-")
        title = a.get("title", "")
        source = a.get("source", "")
        print(f"{date:<{date_w}}  {a.get('id', ''):<40}  {title}")
        print(f"{'':<{date_w}}  {'':<40}  {source}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input", default=str(DEFAULT_INPUT), help="path to articles JSON")
    p.add_argument("-q", "--query", default=None, help="free-text search (AND over terms)")
    p.add_argument("--concept", action="append", default=[], dest="concepts",
                   help="concept filter; repeatable, all must match")
    p.add_argument("--sort", choices=SORT_KEYS, default="registered_date", help="sort key")
    order = p.add_mutually_exclusive_group()
    order.add_argument("--desc", action="store_true", help="descending order (default)")
    order.add_argument("--asc", action="store_true", help="ascending order")
    p.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = p.parse_args(argv)

    path = Path(args.input)
    if not path.is_file():
        print(f"error: input not found: {path}", file=sys.stderr)
        return 1
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        articles = payload["articles"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        print(f"error: invalid articles file: {exc}", file=sys.stderr)
        return 1

    selected = list(articles)
    if args.query:
        selected = [a for a in selected if matches_query(a, args.query)]
    if args.concepts:
        selected = [a for a in selected if matches_concepts(a, args.concepts)]

    descending = not args.asc  # default: descending
    selected = sort_articles(selected, args.sort, descending)

    if args.json:
        print(json.dumps({"articles": selected}, ensure_ascii=False, indent=2))
    else:
        print_rows(selected)
        print(f"\n{len(selected)} / {len(articles)} articles")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
