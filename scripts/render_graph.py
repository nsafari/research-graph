#!/usr/bin/env python3
"""Render the Persian articles JSON into a self-contained interactive graph.html.

Reads ``data/articles.json`` (override with ``--input``), builds an undirected
graph where nodes are articles and edges connect articles that share at least
one concept, then writes a single-file HTML page driven by D3 v7.

Only the Python standard library is used.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REQUIRED_FIELDS = ("id", "title", "source", "concepts", "claim", "reason", "conclusion")
STRING_FIELDS = ("id", "title", "source")
LIST_OF_STR_FIELDS = ("concepts", "claim", "reason", "conclusion")
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


def compute_edges(articles: list[dict]) -> list[tuple[str, str, list[str]]]:
    """Return (source_id, target_id, shared_concepts) for every connected pair."""
    unique_concepts = [list(dict.fromkeys(article["concepts"])) for article in articles]
    concept_sets = [set(concepts) for concepts in unique_concepts]
    edges: list[tuple[str, str, list[str]]] = []
    for i in range(len(articles)):
        for j in range(i + 1, len(articles)):
            shared = [c for c in unique_concepts[j] if c in concept_sets[i]]
            if shared:
                edges.append((articles[i]["id"], articles[j]["id"], shared))
    return edges


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
.links line { stroke: var(--edge); stroke-width: 1.2px; }
.links line.active { stroke: var(--edge-active); stroke-width: 2px; }
.edge-labels text {
  font-size: 10.5px;
  fill: var(--ink-soft);
  pointer-events: none;
  paint-order: stroke;
  stroke: var(--bg);
  stroke-width: 3px;
  stroke-linejoin: round;
}
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
    <div id="legend">«گره = مقاله، پیوند = مفاهیم مشترک»</div>
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
    </div>
  </aside>
</div>
<script>
const DATA = __DATA__;
</script>
<script src="https://cdn.jsdelivr.net/npm/d3@7"></script>
<script>
(function () {
  "use strict";

  var PALETTE = ["#4e6e8e", "#5f7f6b", "#8a6f52", "#7d6480", "#4f7d7c", "#8a6470", "#55708a", "#6b7280"];
  var articles = DATA.articles;
  var byId = new Map(articles.map(function (a) { return [a.id, a]; }));

  // Edges: two articles are linked when they share at least one concept.
  var uniqueConcepts = articles.map(function (a) {
    return Array.from(new Set(a.concepts));
  });
  var conceptSets = uniqueConcepts.map(function (cs) { return new Set(cs); });
  var links = [];
  for (var i = 0; i < articles.length; i++) {
    for (var j = i + 1; j < articles.length; j++) {
      var shared = uniqueConcepts[j].filter(function (c) {
        return conceptSets[i].has(c);
      });
      if (shared.length > 0) {
        links.push({ source: articles[i].id, target: articles[j].id, concepts: shared });
      }
    }
  }

  var degree = new Map(articles.map(function (a) { return [a.id, 0]; }));
  links.forEach(function (l) {
    degree.set(l.source, degree.get(l.source) + 1);
    degree.set(l.target, degree.get(l.target) + 1);
  });

  var nodes = articles.map(function (a) {
    return { id: a.id, article: a, degree: degree.get(a.id) };
  });

  function nodeRadius(d) {
    return Math.max(14, Math.min(28, 14 + 4 * Math.sqrt(d.degree)));
  }

  function truncate(text, max) {
    return text.length > max ? text.slice(0, max) + "…" : text;
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

  var simulation = d3.forceSimulation(nodes)
    .force("link", d3.forceLink(links).id(function (d) { return d.id; }).distance(210))
    .force("charge", d3.forceManyBody().strength(-520).distanceMax(700))
    .force("center", d3.forceCenter(width / 2, height / 2))
    .force("collide", d3.forceCollide(function (d) { return nodeExtent(d); }));

  var linkSel = linkLayer.selectAll("line").data(links).join("line");
  linkSel.append("title").text(function (d) {
    return "مفاهیم مشترک: " + d.concepts.join("، ");
  });

  var labelSel = labelLayer.selectAll("text").data(links).join("text")
    .attr("text-anchor", "middle")
    .text(function (d) { return truncate(d.concepts.join("، "), 48); });

  var nodeSel = nodeLayer.selectAll("g.node").data(nodes, function (d) { return d.id; }).join("g")
    .attr("class", "node")
    .on("click", function (event, d) {
      event.stopPropagation();
      selectArticle(d.id);
    });

  nodeSel.append("circle")
    .attr("r", nodeRadius)
    .attr("fill", function (d, i) { return PALETTE[i % PALETTE.length]; });

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

    labelSel.attr("transform", function (d) {
      var mx = (d.source.x + d.target.x) / 2;
      var my = (d.source.y + d.target.y) / 2 - 6;
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
    conclusion: document.getElementById("d-conclusion")
  };
  var sections = {
    claim: document.getElementById("sec-claim"),
    reason: document.getElementById("sec-reason"),
    conclusion: document.getElementById("sec-conclusion")
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


def build_page(payload: object) -> str:
    return PAGE_TEMPLATE.replace("__DATA__", embed_data(payload), 1)


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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    input_path = Path(args.input)
    output_path = Path(args.output)

    payload = load_payload(input_path)
    articles = validate_articles(payload)
    warn_on_long_articles(articles)
    edges = compute_edges(articles)

    page = build_page(payload)
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(page, encoding="utf-8")
    except OSError as exc:
        fail(f"cannot write output file {output_path}: {exc}")

    print(f"Wrote {args.output} ({len(articles)} articles, {len(edges)} edges)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
