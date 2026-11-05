#!/usr/bin/env python3
"""Render the Persian articles JSON into a self-contained interactive graph.html.

Reads ``data/articles.json`` (override with ``--input``), builds an undirected
graph where nodes are articles and edges connect articles that share at least
one concept, then writes two single-file HTML pages: the article graph driven
by D3 v7, and a light 3D concept graph driven by 3d-force-graph.

Only the Python standard library is used.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

REQUIRED_FIELDS = ("id", "title", "source", "concepts", "claim", "reason", "conclusion")
STRING_FIELDS = ("id", "title", "source")
LIST_OF_STR_FIELDS = ("concepts", "claim", "reason", "conclusion")
OPTIONAL_TEXT_FIELDS = ("application",)
MAX_PARAGRAPHS = 5


def fail(message: str) -> "NoReturn":  # noqa: F821 - stdlib-only friendly
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def load_payload(path: Path) -> object:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        fail(f"input file not found: {path}")
    except IsADirectoryError:
        fail(f"input path is a directory, not a file: {path}")
    except OSError as exc:
        fail(f"cannot read input file {path}: {exc}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON in {path}: {exc}")
    raise AssertionError("unreachable")


def validate_articles(payload: object) -> list[dict]:
    if not isinstance(payload, dict):
        fail("top-level JSON must be an object with an 'articles' array")
    if "articles" not in payload:
        fail("top-level JSON is missing the required 'articles' key")
    articles = payload["articles"]
    if not isinstance(articles, list):
        fail("'articles' must be an array")
    if not articles:
        fail("'articles' must not be empty")

    seen_ids: set[str] = set()
    for index, article in enumerate(articles):
        where = f"articles[{index}]"
        if not isinstance(article, dict):
            fail(f"{where}: each article must be an object")
        for field in REQUIRED_FIELDS:
            if field not in article:
                fail(f"{where}: missing required field '{field}'")
        for field in STRING_FIELDS:
            if not isinstance(article[field], str):
                fail(f"{where}: field '{field}' must be a string")
        if not article["id"]:
            fail(f"{where}: field 'id' must be a non-empty string")
        if article["id"] in seen_ids:
            fail(f"{where}: duplicate id '{article['id']}'")
        seen_ids.add(article["id"])
        for field in LIST_OF_STR_FIELDS:
            value = article[field]
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                fail(f"{where}: field '{field}' must be an array of strings")
        for field in OPTIONAL_TEXT_FIELDS:
            if field in article:
                value = article[field]
                ok_str = isinstance(value, str)
                ok_list = isinstance(value, list) and all(isinstance(i, str) for i in value)
                if not (ok_str or ok_list):
                    fail(f"{where}: field '{field}' must be a string or an array of strings")
    return articles


def warn_on_long_articles(articles: list[dict]) -> None:
    for article in articles:
        total = len(article["claim"]) + len(article["reason"]) + len(article["conclusion"])
        if total > MAX_PARAGRAPHS:
            warn(
                f"article '{article['id']}' has {total} paragraphs across "
                f"claim/reason/conclusion (recommended maximum is {MAX_PARAGRAPHS}); "
                "the detail panel may feel crowded"
            )


GENERIC_CONCEPTS = frozenset({
    "هوش مصنوعی",
    "اتوماسیون کاری",
    "عامل‌های هوش مصنوعی",
})


def compute_weighted_edges(articles: list[dict], min_weight: float) -> list[tuple[str, str, list[str]]]:
    """Return (source_id, target_id, shared_concepts) for pairs whose summed
    IDF weight over shared concepts reaches ``min_weight``.

    IDF = ln(N / df): a concept present in every article gets weight 0, so
    generic hubs (e.g. «هوش مصنوعی») can no longer clique the whole graph.
    Edges that share *only* generic concepts are suppressed entirely.
    Must stay in sync with the identical computation in the page template.
    """
    unique_concepts = [list(dict.fromkeys(article["concepts"])) for article in articles]
    concept_sets = [set(concepts) for concepts in unique_concepts]
    df: dict[str, int] = {}
    for concepts in unique_concepts:
        for c in concepts:
            df[c] = df.get(c, 0) + 1
    n = len(articles)
    edges: list[tuple[str, str, list[str]]] = []
    for i in range(n):
        for j in range(i + 1, n):
            shared = [c for c in unique_concepts[j] if c in concept_sets[i]]
            if not shared:
                continue
            specific = [c for c in shared if c not in GENERIC_CONCEPTS]
            if not specific:
                continue
            weight = sum(math.log(n / df[c]) for c in shared)
            if weight >= min_weight:
                edges.append((articles[i]["id"], articles[j]["id"], shared))
    return edges


def compute_concept_graph(articles: list[dict], parents: dict) -> dict:
    """Build the concept-level graph for the 3D page.

    Nodes are the unique concept strings across all articles (deduped by
    exact string, never normalized, so «هوش مصنوعی» variants never merge);
    an edge joins two concepts that co-occur in at least one article, with
    ``weight`` = number of shared articles and the shared article ids kept
    on the edge. ``parent`` comes from ``load_parents``; a missing entry
    (null/unknown parent) yields ``None`` so the page colors it neutral.
    """
    concept_articles: dict[str, list[str]] = {}
    for article in articles:
        for concept in dict.fromkeys(article["concepts"]):
            concept_articles.setdefault(concept, []).append(article["id"])

    concepts = list(concept_articles)
    concept_sets = {c: set(ids) for c, ids in concept_articles.items()}
    degree = {c: 0 for c in concepts}
    edges: list[dict] = []
    for i in range(len(concepts)):
        for j in range(i + 1, len(concepts)):
            source, target = concepts[i], concepts[j]
            shared = [aid for aid in concept_articles[source] if aid in concept_sets[target]]
            if not shared:
                continue
            degree[source] += 1
            degree[target] += 1
            edges.append({
                "source": source,
                "target": target,
                "weight": len(shared),
                "articles": shared,
            })

    nodes = [
        {
            "id": concept,
            "parent": parents.get(concept),
            "articles": list(concept_articles[concept]),
            "degree": degree[concept],
        }
        for concept in concepts
    ]
    return {"nodes": nodes, "edges": edges}


def embed_data(payload: object) -> str:
    """Serialize payload for a ``<script>`` blob.

    ``html.escape`` would corrupt JSON, so the correct defence here is JSON
    escaping plus neutralising ``<`` (which can only occur inside JSON strings,
    never as structural syntax). That kills ``</script>`` and ``<!--``
    breakouts. U+2028/U+2029 are escaped for older JS parsers.
    """
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    text = text.replace("<", "\\u003c")
    text = text.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return text


PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>گراف مقالات پژوهشی</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css">
<style>
:root {
  --bg: #f4f6f8;
  --panel-bg: #ffffff;
  --ink: #1f2a33;
  --ink-soft: #5a6a78;
  --ink-body: #2b3a47;
  --line: #dfe4e9;
  --edge: #b8c1cb;
  --edge-active: #5f7183;
  --accent: #33566e;
  --chip-bg: #eef1f4;
  --chip-ink: #33424f;
}
* { box-sizing: border-box; }
html, body { height: 100%; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font-family: "Vazirmatn", Tahoma, "Segoe UI", sans-serif;
  font-size: 15px;
  -webkit-font-smoothing: antialiased;
}
#app { display: flex; flex-direction: row; height: 100vh; }
#stage { position: relative; flex: 1 1 auto; min-width: 0; min-height: 0; }
#graph {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  display: block;
  cursor: grab;
}
#graph:active { cursor: grabbing; }
#search {
  position: absolute;
  top: 12px;
  left: 12px;
  display: flex;
  gap: 8px;
  align-items: center;
}
#search-input {
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.95);
  border-radius: 8px;
  padding: 6px 12px;
  font: inherit;
  font-size: 13px;
  width: 17rem;
  max-width: 60vw;
  color: var(--ink);
}
#search-input:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}
#search-count { font-size: 12px; color: var(--ink-soft); white-space: nowrap; }
#legend {
  position: absolute;
  bottom: 12px;
  right: 12px;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 6px 10px;
  font-size: 12px;
  color: var(--ink-soft);
  pointer-events: none;
}
#panel {
  flex: 0 0 22rem;
  max-width: 22rem;
  background: var(--panel-bg);
  border-inline-start: 1px solid var(--line);
  padding: 18px 16px 24px;
  overflow-y: auto;
}
#hint { color: var(--ink-soft); text-align: center; margin-top: 14vh; padding: 0 8px; }
#hint .hint-main { font-size: 17px; color: var(--ink); }
#hint .hint-sub { font-size: 12.5px; margin-top: 10px; line-height: 2; }
#detail[hidden], #hint[hidden] { display: none; }
.panel-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
#d-title { font-size: 18px; line-height: 1.8; margin: 0; font-weight: 700; }
#btn-close {
  flex: none;
  border: 1px solid var(--line);
  background: var(--bg);
  color: var(--ink);
  border-radius: 6px;
  padding: 4px 12px;
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}
#btn-close:hover { background: var(--line); }
#btn-close:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.source-row {
  margin: 12px 0 0;
  font-size: 12px;
  color: var(--ink-soft);
  display: flex;
  gap: 8px;
  align-items: baseline;
  flex-wrap: wrap;
}
.source-row .field-label { flex: none; }
#d-source { word-break: break-all; }
#d-source a { color: var(--accent); text-decoration: none; }
#d-source a:hover { text-decoration: underline; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }
.chip {
  background: var(--chip-bg);
  color: var(--chip-ink);
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 3px 10px;
  font-size: 12px;
  line-height: 1.8;
}
.essay { margin-top: 16px; border-top: 1px solid var(--line); padding-top: 10px; }
.essay[hidden] { display: none; }
.essay h3 { margin: 0 0 6px; font-size: 14px; color: var(--ink); font-weight: 700; }
.essay p { margin: 0 0 8px; font-size: 13.5px; line-height: 2.1; color: var(--ink-body); }
.essay p:last-child { margin-bottom: 0; }
.meta {
  margin-top: 16px;
  border: 1px solid #e8dcc4;
  border-inline-start: 3px solid #a8834c;
  background: #faf6ed;
  border-radius: 8px;
  padding: 10px 12px;
}
.meta[hidden] { display: none; }
.meta h3 {
  margin: 0 0 6px;
  font-size: 13.5px;
  color: #7a5a2e;
  font-weight: 700;
}
.meta p {
  margin: 0 0 8px;
  font-size: 13px;
  line-height: 2.05;
  color: #4a3d28;
}
.meta p:last-child { margin-bottom: 0; }
.links line { stroke: var(--edge); stroke-width: 1.2px; transition: opacity 0.15s; }
.links line.active { stroke: var(--edge-active); stroke-width: 2px; }
.links line.dim { opacity: 0.12; }
.links line.hover { stroke: var(--edge-active); }
.hits line { stroke: transparent; stroke-width: 16px; cursor: help; }
.node { transition: opacity 0.15s; }
.edge-labels text {
  font-size: 10.5px;
  fill: var(--ink-soft);
  pointer-events: none;
  paint-order: stroke;
  stroke: var(--bg);
  stroke-width: 3px;
  stroke-linejoin: round;
  opacity: 0;
  transition: opacity 0.15s;
}
.edge-labels text.visible { opacity: 1; }
.node circle {
  stroke: #ffffff;
  stroke-width: 1.5px;
  cursor: pointer;
}
.node.selected circle { stroke: var(--ink); stroke-width: 2.5px; }
.node text {
  font-size: 11px;
  fill: var(--ink);
  pointer-events: none;
  paint-order: stroke;
  stroke: var(--bg);
  stroke-width: 3.5px;
  stroke-linejoin: round;
}
@media (max-width: 820px) {
  #app { flex-direction: column; height: auto; min-height: 100vh; }
  #stage { height: 56vh; flex: none; }
  #panel {
    flex: none;
    max-width: none;
    width: auto;
    border-inline-start: none;
    border-top: 1px solid var(--line);
  }
}
</style>
</head>
<body>
<div id="app">
  <main id="stage" aria-label="نمودار گراف مقالات">
    <svg id="graph" role="img" aria-label="گراف نیرویی مقالات"></svg>
    <div id="search">
      <input id="search-input" type="search" placeholder="جستجو در عناوین، مفاهیم و متن‌ها..." autocomplete="off" aria-label="جستجو در مقاله‌ها">
      <span id="search-count" aria-live="polite"></span>
    </div>
    <div id="legend">«گره = مقاله، یال = تشابه موضوعی (برای دیدن علت ارتباط، نشانگر را روی یال ببرید)»</div>
  </main>
  <aside id="panel">
    <div id="hint">
      <div class="hint-main">«روی هر گره کلیک کنید»</div>
      <div class="hint-sub">جزئیات مقاله پس از انتخاب، در این‌جا نمایش داده می‌شود.</div>
    </div>
    <div id="detail" hidden>
      <div class="panel-head">
        <h2 id="d-title"></h2>
        <button id="btn-close" type="button">بستن</button>
      </div>
      <p class="source-row"><span class="field-label">منبع</span><span id="d-source"></span></p>
      <div id="d-concepts" class="chips"></div>
      <section class="essay" id="sec-claim">
        <h3>«مدعا»</h3>
        <div id="d-claim"></div>
      </section>
      <section class="essay" id="sec-reason">
        <h3>«دلیل»</h3>
        <div id="d-reason"></div>
      </section>
      <section class="essay" id="sec-conclusion">
        <h3>«نتیجه»</h3>
        <div id="d-conclusion"></div>
      </section>
      <section class="meta" id="sec-application" hidden>
        <h3>«کاربرد، قیمت و محصولات»</h3>
        <div id="d-application"></div>
      </section>
    </div>
  </aside>
</div>
<script>
const DATA = __DATA__;
const PARENTS = __PARENTS__;
const MIN_EDGE_WEIGHT = __MIN_W__;
</script>
<script src="https://cdn.jsdelivr.net/npm/d3@7"></script>
<script>
(function () {
  "use strict";

  var PALETTE = ["#4e6e8e", "#5f7f6b", "#8a6f52", "#7d6480", "#4f7d7c", "#8a6470", "#55708a", "#6b7280"];
  var articles = DATA.articles;
  var byId = new Map(articles.map(function (a) { return [a.id, a]; }));

  // Weighted edges: IDF per concept (ln(N/df)); generic hubs get weight 0,
  // so a concept like «هوش مصنوعی» alone never creates an edge.
  var GENERIC_CONCEPTS = new Set(["هوش مصنوعی", "اتوماسیون کاری", "عامل‌های هوش مصنوعی"]);
  var uniqueConcepts = articles.map(function (a) {
    return Array.from(new Set(a.concepts));
  });
  var df = {};
  uniqueConcepts.forEach(function (cs) {
    cs.forEach(function (c) { df[c] = (df[c] || 0) + 1; });
  });
  function idf(c) { return Math.log(articles.length / df[c]); }
  var links = [];
  for (var i = 0; i < articles.length; i++) {
    for (var j = i + 1; j < articles.length; j++) {
      var shared = uniqueConcepts[j].filter(function (c) {
        return uniqueConcepts[i].indexOf(c) !== -1;
      });
      if (!shared.length) continue;
      var hasSpecific = shared.some(function (c) { return !GENERIC_CONCEPTS.has(c); });
      if (!hasSpecific) continue;
      var w = 0;
      shared.forEach(function (c) { w += idf(c); });
      if (w >= MIN_EDGE_WEIGHT) {
        links.push({ source: articles[i].id, target: articles[j].id, concepts: shared, weight: w });
      }
    }
  }

  // Topic = parent of the rarest concept (falls back to the concept itself).
  // Same-topic articles share a cluster anchor; node colour follows the topic.
  function topicOf(a) {
    var cs = Array.from(new Set(a.concepts));
    cs.sort(function (x, y) { return (df[x] || 0) - (df[y] || 0); });
    var rarest = cs[0];
    return PARENTS[rarest] || rarest;
  }
  var topics = Array.from(new Set(articles.map(topicOf)));
  var topicIndex = new Map(topics.map(function (t, i) { return [t, i]; }));

  var degree = new Map(articles.map(function (a) { return [a.id, 0]; }));
  links.forEach(function (l) {
    degree.set(l.source, degree.get(l.source) + 1);
    degree.set(l.target, degree.get(l.target) + 1);
  });

  var nodes = articles.map(function (a) {
    return { id: a.id, article: a, degree: degree.get(a.id), topic: topicOf(a) };
  });

  function nodeRadius(d) {
    return Math.max(14, Math.min(28, 14 + 4 * Math.sqrt(d.degree)));
  }

  function truncate(text, max) {
    return text.length > max ? text.slice(0, max) + "…" : text;
  }

  function faNum(s) {
    return String(s).replace(/[0-9]/g, function (d) { return "۰۱۲۳۴۵۶۷۸۹"[+d]; });
  }

  // Edge label: only distinctive shared concepts, ranked by IDF.
  // A concept whose IDF is under 30% of the edge's strongest concept is a
  // hub-ish extra (e.g. «هوش مصنوعی» next to «عامل همیشه‌فعال») and is
  // dropped; at most 3 concepts are listed, overflow becomes «+n», and the
  // summed edge weight is appended so the strength of the link is visible.
  function edgeLabelText(d) {
    var byIdf = d.concepts.slice().sort(function (a, b) { return idf(b) - idf(a); });
    var maxIdf = idf(byIdf[0]);
    var strong = byIdf.filter(function (c) { return idf(c) >= 0.3 * maxIdf; });
    if (!strong.length) strong = byIdf.slice(0, 1);
    var shown = strong.slice(0, 3);
    var extra = strong.length - shown.length;
    var text = shown.join("، ") + (extra > 0 ? " +" + extra : "");
    return truncate(text, 36) + " (وزن " + faNum(d.weight.toFixed(1)) + ")";
  }

  // Wrap a title into short lines so labels stay compact and readable in RTL.
  // Widths are tuned for the 11px Vazirmatn used by .node text.
  var TITLE_MAX_CHARS = 18;
  var TITLE_MAX_LINES = 3;
  function wrapTitle(text, maxChars, maxLines) {
    var words = String(text).split(" ");
    var lines = [];
    var cur = "";
    for (var i = 0; i < words.length; i++) {
      var word = words[i];
      var candidate = cur ? cur + " " + word : word;
      if (candidate.length > maxChars && cur) {
        lines.push(cur);
        cur = word;
      } else {
        cur = candidate;
      }
    }
    if (cur) lines.push(cur);
    if (lines.length > maxLines) {
      lines = lines.slice(0, maxLines);
      lines[maxLines - 1] = truncate(lines[maxLines - 1], maxChars - 1) + "…";
    }
    return lines;
  }

  // Height reserved for a node's label block (used by the collide force so
  // circles + text never overlap even when the simulation settles tight).
  var LABEL_LINE_PX = 13;
  var LABEL_GAP_PX = 19;
  function labelLines(d) {
    if (!d._lines) d._lines = wrapTitle(d.article.title, TITLE_MAX_CHARS, TITLE_MAX_LINES);
    return d._lines;
  }
  function labelHeight(d) {
    return labelLines(d).length * LABEL_LINE_PX;
  }
  function nodeExtent(d) {
    return nodeRadius(d) + LABEL_GAP_PX + labelHeight(d) / 2;
  }

  var svg = d3.select("#graph");
  var root = svg.append("g");
  var linkLayer = root.append("g").attr("class", "links");
  var hitLayer = root.append("g").attr("class", "hits");
  var labelLayer = root.append("g").attr("class", "edge-labels");
  var nodeLayer = root.append("g").attr("class", "nodes");

  var width = 10;
  var height = 10;
  function measure() {
    var rect = document.getElementById("stage").getBoundingClientRect();
    width = Math.max(10, rect.width);
    height = Math.max(10, rect.height);
    svg.attr("viewBox", "0 0 " + width + " " + height);
  }
  measure();

  // Seed positions around the centre so single/empty graphs start sensibly.
  nodes.forEach(function (d, i) {
    var angle = (i / Math.max(nodes.length, 1)) * 2 * Math.PI;
    var spread = Math.min(width, height) / 3;
    d.x = width / 2 + Math.cos(angle) * spread;
    d.y = height / 2 + Math.sin(angle) * spread;
  });

  function anchorAngle(d) {
    var i = topicIndex.has(d.topic) ? topicIndex.get(d.topic) : 0;
    return (i / Math.max(topics.length, 1)) * 2 * Math.PI - Math.PI / 2;
  }
  function anchorRadius() { return Math.min(width, height) / 3; }

  var simulation = d3.forceSimulation(nodes)
    .force("link", d3.forceLink(links).id(function (d) { return d.id; }).distance(210))
    .force("charge", d3.forceManyBody().strength(-520).distanceMax(700))
    .force("center", d3.forceCenter(width / 2, height / 2))
    .force("topicX", d3.forceX(function (d) { return width / 2 + Math.cos(anchorAngle(d)) * anchorRadius(); }).strength(0.07))
    .force("topicY", d3.forceY(function (d) { return height / 2 + Math.sin(anchorAngle(d)) * anchorRadius(); }).strength(0.07))
    .force("collide", d3.forceCollide(function (d) { return nodeExtent(d); }));

  var linkSel = linkLayer.selectAll("line").data(links).join("line")
    .attr("stroke-width", function (d) { return Math.min(4, 1 + d.weight); });

  // Edge labels exist but stay hidden by default; they appear only on hover
  // over the matching edge hit-area, so the graph is not covered in text.
  var labelSel = labelLayer.selectAll("text").data(links).join("text")
    .attr("text-anchor", "middle")
    .text(function (d) { return edgeLabelText(d); });

  // Wide invisible hit-area per edge (SVG lines are too thin to hover reliably).
  var hitSel = hitLayer.selectAll("line").data(links).join("line")
    .on("mouseenter", function (event, d) {
      var i = links.indexOf(d);
      d3.select(labelSel.nodes()[i]).classed("visible", true);
      d3.select(linkSel.nodes()[i]).classed("hover", true);
    })
    .on("mouseleave", function (event, d) {
      var i = links.indexOf(d);
      d3.select(labelSel.nodes()[i]).classed("visible", false);
      d3.select(linkSel.nodes()[i]).classed("hover", false);
    });

  var nodeSel = nodeLayer.selectAll("g.node").data(nodes, function (d) { return d.id; }).join("g")
    .attr("class", "node")
    .on("click", function (event, d) {
      event.stopPropagation();
      selectArticle(d.id);
    });

  nodeSel.append("circle")
    .attr("r", nodeRadius)
    .attr("fill", function (d) { return PALETTE[(topicIndex.get(d.topic) || 0) % PALETTE.length]; });

  nodeSel.append("text")
    .attr("text-anchor", "middle")
    .attr("y", function (d) { return nodeRadius(d) + 15; })
    .selectAll("tspan")
    .data(function (d) { return labelLines(d); })
    .join("tspan")
    .attr("x", 0)
    .attr("dy", function (d, i) { return i === 0 ? 0 : LABEL_LINE_PX; })
    .text(function (d) { return d; });

  nodeSel.append("title").text(function (d) { return d.article.title; });

  var dragBehaviour = d3.drag()
    .on("start", function (event, d) {
      if (!event.active) simulation.alphaTarget(0.25).restart();
      d.fx = d.x;
      d.fy = d.y;
    })
    .on("drag", function (event, d) {
      d.fx = event.x;
      d.fy = event.y;
    })
    .on("end", function (event, d) {
      if (!event.active) simulation.alphaTarget(0);
      d.fx = null;
      d.fy = null;
    });
  nodeSel.call(dragBehaviour);

  var zoomBehaviour = d3.zoom()
    .scaleExtent([0.3, 5])
    .on("zoom", function (event) {
      root.attr("transform", event.transform);
    });
  svg.call(zoomBehaviour);

  simulation.on("tick", function () {
    linkSel
      .attr("x1", function (d) { return d.source.x; })
      .attr("y1", function (d) { return d.source.y; })
      .attr("x2", function (d) { return d.target.x; })
      .attr("y2", function (d) { return d.target.y; });

    hitSel
      .attr("x1", function (d) { return d.source.x; })
      .attr("y1", function (d) { return d.source.y; })
      .attr("x2", function (d) { return d.target.x; })
      .attr("y2", function (d) { return d.target.y; });

    labelSel.attr("transform", function (d, i) {
      var mx = (d.source.x + d.target.x) / 2;
      var my = (d.source.y + d.target.y) / 2 - 8 + (i % 2) * 30;
      return "translate(" + mx + "," + my + ")";
    });

    nodeSel.attr("transform", function (d) {
      return "translate(" + d.x + "," + d.y + ")";
    });
  });

  var hintEl = document.getElementById("hint");
  var detailEl = document.getElementById("detail");
  var titleEl = document.getElementById("d-title");
  var sourceEl = document.getElementById("d-source");
  var conceptsEl = document.getElementById("d-concepts");
  var paras = {
    claim: document.getElementById("d-claim"),
    reason: document.getElementById("d-reason"),
    conclusion: document.getElementById("d-conclusion"),
    application: document.getElementById("d-application")
  };
  var sections = {
    claim: document.getElementById("sec-claim"),
    reason: document.getElementById("sec-reason"),
    conclusion: document.getElementById("sec-conclusion"),
    application: document.getElementById("sec-application")
  };
  var selectedId = null;

  function fillParagraphs(container, list) {
    container.replaceChildren();
    list.forEach(function (text) {
      var p = document.createElement("p");
      p.textContent = text;
      container.appendChild(p);
    });
    return list.length > 0;
  }

  function fillApplication(container, value) {
    var list = typeof value === "string" ? [value] : (Array.isArray(value) ? value : []);
    return fillParagraphs(container, list);
  }

  function selectArticle(id) {
    selectedId = id;
    nodeSel.classed("selected", function (d) { return d.id === id; });
    linkSel.classed("active", function (d) {
      return id !== null && (d.source.id === id || d.target.id === id);
    });

    if (id === null) {
      detailEl.hidden = true;
      hintEl.hidden = false;
      return;
    }

    var article = byId.get(id);
    hintEl.hidden = true;
    detailEl.hidden = false;
    titleEl.textContent = article.title;

    sourceEl.replaceChildren();
    sourceEl.removeAttribute("dir");
    if (/^https?:\/\//i.test(article.source)) {
      sourceEl.setAttribute("dir", "ltr");
      var link = document.createElement("a");
      link.href = article.source;
      link.textContent = article.source;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      sourceEl.appendChild(link);
    } else {
      sourceEl.textContent = article.source;
    }

    conceptsEl.replaceChildren();
    article.concepts.forEach(function (concept) {
      var chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = concept;
      conceptsEl.appendChild(chip);
    });

    sections.claim.hidden = !fillParagraphs(paras.claim, article.claim);
    sections.reason.hidden = !fillParagraphs(paras.reason, article.reason);
    sections.conclusion.hidden = !fillParagraphs(paras.conclusion, article.conclusion);
    sections.application.hidden = !fillApplication(paras.application, article.application);
  }

  document.getElementById("btn-close").addEventListener("click", function () {
    selectArticle(null);
  });

  // Clear selection on background clicks, but ignore the click that ends a pan.
  var downPoint = null;
  svg.on("pointerdown", function (event) {
    downPoint = [event.clientX, event.clientY];
  });
  svg.on("click", function (event) {
    if (downPoint !== null) {
      var dx = event.clientX - downPoint[0];
      var dy = event.clientY - downPoint[1];
      downPoint = null;
      if (dx * dx + dy * dy > 25) return;
    }
    if (selectedId !== null) selectArticle(null);
  });

  var textById = new Map(articles.map(function (a) {
    var parts = [a.title].concat(a.concepts, a.claim, a.reason, a.conclusion);
    if (typeof a.application === "string") parts.push(a.application);
    else if (Array.isArray(a.application)) parts = parts.concat(a.application);
    return [a.id, parts.join(" ").toLowerCase()];
  }));

  function toFaDigits(n) {
    return String(n).replace(/\d/g, function (d) { return "۰۱۲۳۴۵۶۷۸۹"[d]; });
  }

  var searchInput = document.getElementById("search-input");
  var searchCount = document.getElementById("search-count");

  function applySearch() {
    var q = searchInput.value.trim().toLowerCase();
    var matches = 0;
    nodes.forEach(function (d) {
      d._searchMatch = !q || textById.get(d.id).indexOf(q) !== -1;
      if (q && d._searchMatch) matches++;
    });
    nodeSel.style("opacity", function (d) { return d._searchMatch ? 1 : 0.15; });
    function bothMatch(d) { return d.source._searchMatch && d.target._searchMatch; }
    linkSel.classed("dim", function (d) { return q && !bothMatch(d); });
    labelSel.style("opacity", function (d) { return q && !bothMatch(d) ? 0.15 : 1; });
    searchCount.textContent = q ? toFaDigits(matches) + " نتیجه" : "";
  }

  searchInput.addEventListener("input", applySearch);
  searchInput.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      searchInput.value = "";
      applySearch();
    } else if (event.key === "Enter") {
      var first = nodes.find(function (d) { return d._searchMatch; });
      if (first) selectArticle(first.id);
    }
  });

  window.addEventListener("resize", function () {
    measure();
    simulation.force("center", d3.forceCenter(width / 2, height / 2));
    simulation.alpha(0.25).restart();
  });
})();
</script>
</body>
</html>
"""


CONCEPT_PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>گراف مفاهیم پژوهشی</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css">
<style>
:root {
  --bg: #f4f6f8;
  --panel-bg: #ffffff;
  --ink: #1f2a33;
  --ink-soft: #5a6a78;
  --ink-body: #2b3a47;
  --line: #dfe4e9;
  --accent: #33566e;
}
* { box-sizing: border-box; }
html, body { height: 100%; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font-family: "Vazirmatn", Tahoma, "Segoe UI", sans-serif;
  font-size: 15px;
  -webkit-font-smoothing: antialiased;
}
#app { display: flex; flex-direction: row; height: 100vh; }
#stage { position: relative; flex: 1 1 auto; min-width: 0; min-height: 0; }
#graph { position: absolute; inset: 0; }
#legend {
  position: absolute;
  bottom: 12px;
  right: 12px;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 6px 10px;
  font-size: 12px;
  color: var(--ink-soft);
  pointer-events: none;
  max-width: min(24rem, 72vw);
  line-height: 1.9;
}
#legend-items {
  display: flex;
  flex-wrap: wrap;
  gap: 2px 12px;
  margin-top: 4px;
}
.legend-item { display: inline-flex; align-items: center; gap: 5px; }
.dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  flex: none;
  border: 1px solid rgba(31, 42, 51, 0.2);
}
#panel {
  flex: 0 0 22rem;
  max-width: 22rem;
  background: var(--panel-bg);
  border-inline-start: 1px solid var(--line);
  padding: 18px 16px 24px;
  overflow-y: auto;
}
#hint { color: var(--ink-soft); text-align: center; margin-top: 14vh; padding: 0 8px; }
#hint .hint-main { font-size: 17px; color: var(--ink); }
#hint .hint-sub { font-size: 12.5px; margin-top: 10px; line-height: 2; }
#detail[hidden], #hint[hidden] { display: none; }
.panel-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
#d-title { font-size: 18px; line-height: 1.8; margin: 0; font-weight: 700; }
#btn-close {
  flex: none;
  border: 1px solid var(--line);
  background: var(--bg);
  color: var(--ink);
  border-radius: 6px;
  padding: 4px 12px;
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}
#btn-close:hover { background: var(--line); }
#btn-close:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.meta-row {
  margin: 12px 0 0;
  font-size: 12.5px;
  color: var(--ink-soft);
  display: flex;
  gap: 8px;
  align-items: baseline;
  flex-wrap: wrap;
}
.meta-row .field-label { flex: none; }
.meta-row strong { color: var(--ink); font-weight: 600; }
#d-parent { color: var(--accent); }
#d-parent.neutral { color: var(--ink-soft); font-weight: 400; }
.art-head {
  margin: 16px 0 4px;
  border-top: 1px solid var(--line);
  padding-top: 10px;
  font-size: 14px;
  font-weight: 700;
}
#d-articles { list-style: none; margin: 0; padding: 0; }
#d-articles li { padding: 8px 0; border-bottom: 1px solid var(--line); }
#d-articles li:last-child { border-bottom: none; }
#d-articles .a-title { font-size: 13.5px; line-height: 1.9; color: var(--ink-body); }
#d-articles .a-source {
  display: block;
  margin-top: 2px;
  font-size: 11.5px;
  color: var(--ink-soft);
  word-break: break-all;
}
#d-articles .a-source a { color: var(--accent); text-decoration: none; }
#d-articles .a-source a:hover { text-decoration: underline; }
.scene-tooltip {
  font-family: "Vazirmatn", Tahoma, "Segoe UI", sans-serif;
  direction: rtl;
  background: rgba(255, 255, 255, 0.96);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 6px 10px;
  color: var(--ink);
  font-size: 12.5px;
  line-height: 1.9;
  max-width: 22rem;
}
.scene-tooltip strong { font-size: 13px; }
.scene-tooltip .tip-sub { color: var(--ink-soft); font-size: 11.5px; }
.scene-tooltip .tip-item { color: var(--ink-body); font-size: 12px; }
@media (max-width: 820px) {
  #app { flex-direction: column; height: auto; min-height: 100vh; }
  #stage { height: 56vh; flex: none; }
  #panel {
    flex: none;
    max-width: none;
    width: auto;
    border-inline-start: none;
    border-top: 1px solid var(--line);
  }
}
</style>
</head>
<body>
<div id="app">
  <main id="stage" aria-label="نمودار سه‌بعدی مفاهیم">
    <div id="graph"></div>
    <div id="legend">
      «گره = مفهوم، یال = اشتراک در دست‌کم یک مقاله»
      <div id="legend-items"></div>
    </div>
  </main>
  <aside id="panel">
    <div id="hint">
      <div class="hint-main">«روی هر گره کلیک کنید»</div>
      <div class="hint-sub">فهرست مقاله‌هایی که از این مفهوم استفاده کرده‌اند، پس از انتخاب، در این‌جا نمایش داده می‌شود.</div>
    </div>
    <div id="detail" hidden>
      <div class="panel-head">
        <h2 id="d-title"></h2>
        <button id="btn-close" type="button">بستن</button>
      </div>
      <p class="meta-row"><span class="field-label">دسته</span><strong id="d-parent"></strong></p>
      <p class="meta-row"><span class="field-label">تعداد مقاله</span><strong id="d-count"></strong></p>
      <h3 class="art-head">«مقاله‌های این مفهوم»</h3>
      <ul id="d-articles"></ul>
    </div>
  </aside>
</div>
<script>
const DATA = __DATA__;
const GRAPH = __GRAPH__;
</script>
<script src="assets/vendor/3d-force-graph.min.js"></script>
<script>
if (typeof ForceGraph3D !== "function") {
  document.getElementById("hint").innerHTML =
    '<div class="hint-main">«کتابخانه‌ی گراف سه‌بعدی بارگذاری نشد»</div>' +
    '<div class="hint-sub">فایل assets/vendor/3d-force-graph.min.js کنار صفحه لازم است. صفحه را از ریشه‌ی پروژه باز کنید یا فایل vendor را در کنار HTML بگذارید.</div>';
}
</script>
<script>
(function () {
  "use strict";

  if (typeof ForceGraph3D !== "function") {
    return;
  }

  var PALETTE = ["#4e6e8e", "#5f7f6b", "#8a6f52", "#7d6480", "#4f7d7c", "#8a6470", "#55708a", "#6b7280"];
  var NEUTRAL = "#9aa3ad";
  var articles = DATA.articles;
  var byId = new Map(articles.map(function (a) { return [a.id, a]; }));
  var nodes = GRAPH.nodes;
  var links = GRAPH.edges;

  // One colour per parent topic that actually appears; concepts with a null
  // or missing parent stay neutral gray.
  var parentsSeen = [];
  nodes.forEach(function (n) {
    if (n.parent && parentsSeen.indexOf(n.parent) === -1) parentsSeen.push(n.parent);
  });
  var colorByParent = new Map(parentsSeen.map(function (p, i) {
    return [p, PALETTE[i % PALETTE.length]];
  }));
  function colorOf(n) {
    return n.parent ? (colorByParent.get(n.parent) || NEUTRAL) : NEUTRAL;
  }

  function faNum(s) {
    return String(s).replace(/[0-9]/g, function (d) { return "۰۱۲۳۴۵۶۷۸۹"[+d]; });
  }

  // Tooltips are injected as HTML by 3d-force-graph, so escape every value.
  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  var legendItems = document.getElementById("legend-items");
  function addLegendItem(label, color) {
    var item = document.createElement("span");
    item.className = "legend-item";
    var dot = document.createElement("span");
    dot.className = "dot";
    dot.style.background = color;
    item.appendChild(dot);
    item.appendChild(document.createTextNode(label));
    legendItems.appendChild(item);
  }
  parentsSeen.forEach(function (p) { addLegendItem(p, colorByParent.get(p)); });
  if (nodes.some(function (n) { return !n.parent; })) {
    addLegendItem("بدون دسته", NEUTRAL);
  }

  var hintEl = document.getElementById("hint");
  var detailEl = document.getElementById("detail");
  var titleEl = document.getElementById("d-title");
  var parentEl = document.getElementById("d-parent");
  var countEl = document.getElementById("d-count");
  var listEl = document.getElementById("d-articles");

  function selectConcept(node) {
    if (!node) {
      detailEl.hidden = true;
      hintEl.hidden = false;
      return;
    }
    hintEl.hidden = true;
    detailEl.hidden = false;
    titleEl.textContent = node.id;
    parentEl.textContent = node.parent || "بدون دسته";
    parentEl.classList.toggle("neutral", !node.parent);
    countEl.textContent = faNum(node.articles.length) + " مقاله";

    listEl.replaceChildren();
    node.articles.forEach(function (id) {
      var a = byId.get(id);
      if (!a) return;
      var li = document.createElement("li");
      var title = document.createElement("div");
      title.className = "a-title";
      title.textContent = a.title;
      li.appendChild(title);
      var source = document.createElement("span");
      source.className = "a-source";
      if (/^https?:\/\//i.test(a.source)) {
        source.setAttribute("dir", "ltr");
        var link = document.createElement("a");
        link.href = a.source;
        link.textContent = a.source;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        source.appendChild(link);
      } else {
        source.textContent = a.source;
      }
      li.appendChild(source);
      listEl.appendChild(li);
    });
  }

  document.getElementById("btn-close").addEventListener("click", function () {
    selectConcept(null);
  });
  window.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && !detailEl.hidden) selectConcept(null);
  });

  var stageEl = document.getElementById("stage");
  var Graph = ForceGraph3D()(document.getElementById("graph"))
    .width(stageEl.clientWidth)
    .height(stageEl.clientHeight)
    .backgroundColor("#f4f6f8")
    .graphData({ nodes: nodes, links: links })
    .nodeId("id")
    .nodeVal(function (d) { return 1 + d.articles.length; })
    .nodeColor(colorOf)
    .nodeLabel(function (d) {
      var sub = (d.parent || "بدون دسته") + " · " + faNum(d.articles.length) + " مقاله";
      return '<div><strong>' + esc(d.id) + '</strong><div class="tip-sub">' + esc(sub) + '</div></div>';
    })
    .linkWidth(function (l) { return 0.5 + Math.min(4, l.weight); })
    .linkColor("rgba(95, 113, 131, 0.55)")
    .linkLabel(function (l) {
      var titles = l.articles.map(function (id) {
        var a = byId.get(id);
        return a ? a.title : id;
      });
      var head = faNum(l.weight) + " مقاله مشترک";
      var body = titles.map(function (t) {
        return '<div class="tip-item">' + esc(t) + '</div>';
      }).join("");
      return '<div><strong>' + esc(head) + '</strong>' + body + '</div>';
    })
    .onNodeClick(function (d) { selectConcept(d); })
    .onBackgroundClick(function () {
      if (!detailEl.hidden) selectConcept(null);
    });

  var fitted = false;
  Graph.onEngineStop(function () {
    if (!fitted) {
      fitted = true;
      Graph.zoomToFit(600);
    }
  });

  window.addEventListener("resize", function () {
    Graph.width(stageEl.clientWidth).height(stageEl.clientHeight);
  });

  var navInfo = document.querySelector(".scene-nav-info");
  if (navInfo) {
    navInfo.textContent = "چرخش: کلیک چپ · بزرگ‌نمایی: غلتک ماوس · جابه‌جایی: کلیک راست";
  }
})();
</script>
</body>
</html>
"""


def build_page(payload: object, parents: dict, min_edge_weight: float) -> str:
    page = PAGE_TEMPLATE.replace("__DATA__", embed_data(payload), 1)
    page = page.replace("__PARENTS__", embed_data(parents), 1)
    page = page.replace("__MIN_W__", repr(min_edge_weight), 1)
    return page


def build_concept_page(payload: object, concept_graph: dict) -> str:
    page = CONCEPT_PAGE_TEMPLATE.replace("__DATA__", embed_data(payload), 1)
    page = page.replace("__GRAPH__", embed_data(concept_graph), 1)
    return page


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render the Persian articles JSON into a self-contained interactive graph.html."
    )
    parser.add_argument(
        "--input",
        default="data/articles.json",
        help="path to the articles JSON file (default: data/articles.json)",
    )
    parser.add_argument(
        "--output",
        default="graph.html",
        help="path of the HTML file to write (default: graph.html)",
    )
    parser.add_argument(
        "--output-3d",
        default="graph-concepts3d.html",
        help="path of the 3D concept graph HTML file (default: graph-concepts3d.html)",
    )
    parser.add_argument(
        "--concepts",
        default="concepts.json",
        help="concept-parent taxonomy file (default: concepts.json; missing file = flat taxonomy)",
    )
    parser.add_argument(
        "--min-edge-weight",
        type=float,
        default=0.5,
        help="minimum summed IDF weight for an edge to be drawn (default: 0.5)",
    )
    return parser.parse_args(argv)


def load_parents(path: Path) -> dict:
    if not path.is_file():
        warn(f"taxonomy file not found: {path} (using a flat concept hierarchy)")
        return {}
    payload = load_payload(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("parents"), dict):
        fail(f"{path} must be an object with a 'parents' object")
    parents = {}
    for child, parent in payload["parents"].items():
        if not isinstance(child, str) or (parent is not None and not isinstance(parent, str)):
            fail(f"{path}: 'parents' must map strings to strings or null")
        if parent is not None:
            parents[child] = parent
    return parents


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    input_path = Path(args.input)
    output_path = Path(args.output)
    output_3d_path = Path(args.output_3d)

    payload = load_payload(input_path)
    articles = validate_articles(payload)
    warn_on_long_articles(articles)
    parents = load_parents(Path(args.concepts))
    edges = compute_weighted_edges(articles, args.min_edge_weight)
    concept_graph = compute_concept_graph(articles, parents)

    page = build_page(payload, parents, args.min_edge_weight)
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(page, encoding="utf-8")
    except OSError as exc:
        fail(f"cannot write output file {output_path}: {exc}")

    concept_page = build_concept_page(payload, concept_graph)
    try:
        output_3d_path.parent.mkdir(parents=True, exist_ok=True)
        output_3d_path.write_text(concept_page, encoding="utf-8")
    except OSError as exc:
        fail(f"cannot write output file {output_3d_path}: {exc}")

    print(
        f"Wrote {args.output} ({len(articles)} articles, {len(edges)} edges) and "
        f"{args.output_3d} ({len(concept_graph['nodes'])} concepts, "
        f"{len(concept_graph['edges'])} edges)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
