#!/usr/bin/env python3
"""Fetch paper sources for standalone literature-search.

The acquisition ladder is arXiv LaTeXML HTML, arXiv PDF, optional arXiv source,
and then an open publisher PDF for non-arXiv identifiers. No PDF conversion
engine is imported or invoked here. Install the separate `mineru-pdf` skill when
layout-aware PDF conversion is needed.

Artifacts are stored under the selected litsearch workspace in `papers/`, with
URL, status, timestamp, size, and SHA-256 recorded in `papers/_fetch/<key>.json`.
"""

import datetime as dt
import hashlib
import html
import html.parser
import json
import re
import urllib.error
import urllib.parse
import urllib.request

TOOL_VERSION = "2.0.0"
ARXIV_HTML = "https://arxiv.org/html/{id}"
ARXIV_PDF = "https://arxiv.org/pdf/{id}"
ARXIV_SRC = "https://export.arxiv.org/e-print/{id}"
UA = f"litfetch/{TOOL_VERSION} (standalone-literature-search)"


# --------------------------------------------------------------------- helpers

def _sha256(b):
    return hashlib.sha256(b).hexdigest()


def _get(url, accept=None, timeout=90):
    """One GET. Returns (status, body_bytes, content_type, final_url).

    Never raises on an HTTP error status; a 404 on a channel is information the
    ladder needs, not a failure of the run.
    """
    hdrs = {"User-Agent": UA}
    if accept:
        hdrs["Accept"] = accept
    req = urllib.request.Request(url, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as rsp:
            return rsp.status, rsp.read(), rsp.headers.get("Content-Type", ""), rsp.url
    except urllib.error.HTTPError as e:
        return e.code, b"", e.headers.get("Content-Type", "") if e.headers else "", url
    except urllib.error.URLError as e:
        return 0, b"", f"urlerror: {e.reason}", url


ARXIV_RE = re.compile(r"(?:arxiv[:/]|abs/|pdf/)?(\d{4}\.\d{4,5})(v\d+)?", re.I)


def norm_arxiv(s):
    """Pull a bare arXiv id out of anything a human or an API might hand us."""
    if not s:
        return None
    m = ARXIV_RE.search(str(s).strip())
    return m.group(1) if m else None


def slugify(s, n=40):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", (s or "").lower())).strip("-")[:n]


# ------------------------------------------------- LaTeXML HTML -> markdown

class _LatexmlToMarkdown(html.parser.HTMLParser):
    """Convert arXiv's LaTeXML HTML to markdown without losing what we read for.

    What it is built to preserve, because each one is something a paper note is
    required to carry:
      - section structure and LaTeXML's section ids, emitted as anchors, so a
        quote can cite `S4.SS2` instead of an unverifiable page number;
      - tables, as pipe tables, because a results table is usually the claim;
      - figure and table captions, which the S2 snippet channel drops;
      - formulas, taken from @alttext, which is the authors' original LaTeX.
    """

    SKIP = {"script", "style", "noscript"}
    # LaTeXML wraps display equations in a table. Treating those as data tables
    # produces two-cell garbage rows, so they are detected and unwrapped.
    EQ_CLASSES = ("ltx_equation", "ltx_eqn_table", "ltx_eqn_row")

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []          # finished blocks
        self.buf = []          # current inline run
        self.skip_depth = 0
        self.table = None      # {"rows": [[cell,...]], "row": [...], "cell": [...]}
        self.table_depth = 0
        self.in_cell = False
        self.pending_anchor = None
        self.list_depth = 0

    # -- inline buffer ----------------------------------------------------
    def _flush(self, prefix="", suffix=""):
        text = re.sub(r"[ \t]+", " ", "".join(self.buf)).strip()
        self.buf = []
        if text:
            self.out.append(f"{prefix}{text}{suffix}")

    def _emit(self, s):
        (self.table["cell"] if self.in_cell else self.buf).append(s)

    # -- tags -------------------------------------------------------------
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class", "") or ""

        if tag in self.SKIP:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return

        # Math: @alttext holds the source LaTeX. Prefer it over the MathML text,
        # which flattens to unreadable symbol soup.
        if tag == "math":
            alt = a.get("alttext")
            if alt:
                self._emit(f" ${alt}$ ")
                self.skip_depth += 1   # suppress the MathML body
            return

        if tag == "table":
            self.table_depth += 1
            if self.table_depth == 1 and not any(c in cls for c in self.EQ_CLASSES):
                # Grid placement, per the HTML table model, rather than a list of
                # rows. A results table almost always spans its headers - "L2 (m)"
                # over 1s/2s/3s/Avg (colspan), "Method" down two header rows
                # (rowspan) - and appending cells in document order silently
                # shifts every sub-header left of the numbers it labels. A table
                # that is extracted but misaligned is the one way this converter
                # could lie about a number, so the spans are honoured exactly.
                self.table = {"grid": {}, "occupied": set(), "r": -1, "c": 0,
                              "cell": [], "span": (1, 1)}
            return
        if self.table is not None:
            if tag == "tr":
                self.table["r"] += 1
                self.table["c"] = 0
                return
            if tag in ("td", "th"):
                self.in_cell = True
                self.table["cell"] = []
                def _span(name):
                    try:
                        return max(1, int(a.get(name, 1)))
                    except ValueError:
                        return 1
                self.table["span"] = (_span("colspan"), _span("rowspan"))
                return

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._flush()
            self.buf = []
            self._level = int(tag[1])
            return

        if tag in ("section", "div") and a.get("id") and "ltx_" in cls:
            # Remember the id; attach it to the next heading as a citable anchor.
            self.pending_anchor = a["id"]
            return

        if tag in ("p", "div") and "ltx_para" in cls:
            self._flush()
            return
        if tag == "li":
            self._flush()
            self.buf.append("- ")
            return
        if tag in ("figcaption", "caption"):
            self._flush()
            self._cap = True
            return
        if tag == "br":
            self._emit(" ")

    def handle_endtag(self, tag):
        if tag in self.SKIP or tag == "math":
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self.skip_depth:
            return

        if tag == "table":
            if self.table_depth == 1 and self.table is not None:
                self._close_table()
            self.table_depth = max(0, self.table_depth - 1)
            return
        if self.table is not None:
            if tag in ("td", "th"):
                t = self.table
                text = re.sub(r"\s+", " ", "".join(t["cell"])).strip().replace("|", "\\|")
                colspan, rowspan = t["span"]
                r, c = t["r"], t["c"]
                while (r, c) in t["occupied"]:   # a rowspan from above holds this column
                    c += 1
                for dc in range(colspan):
                    t["grid"][(r, c + dc)] = text if dc == 0 else ""
                    for dr in range(1, rowspan):
                        t["occupied"].add((r + dr, c + dc))
                        t["grid"].setdefault((r + dr, c + dc), "")
                t["c"] = c + colspan
                t["cell"] = []
                t["span"] = (1, 1)
                self.in_cell = False
                return
            if tag == "tr":
                return

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            lvl = getattr(self, "_level", 2)
            anchor = f"  <!-- anchor: {self.pending_anchor} -->" if self.pending_anchor else ""
            self._flush(prefix="#" * lvl + " ", suffix=anchor)
            self.pending_anchor = None
            return
        if tag in ("figcaption", "caption"):
            self._flush(prefix="**", suffix="**")
            self._cap = False
            return
        if tag in ("p", "li"):
            self._flush()

    def handle_data(self, d):
        if self.skip_depth or not d:
            return
        self._emit(d)

    def _close_table(self):
        t, self.table = self.table, None
        if not t["grid"]:
            return
        n_rows = max(r for r, _ in t["grid"]) + 1
        width = max(c for _, c in t["grid"]) + 1
        rows = [[t["grid"].get((r, c), "") for c in range(width)] for r in range(n_rows)]
        rows = [r for r in rows if any(c.strip() for c in r)]
        if not rows:
            return
        self._flush()
        lines = ["| " + " | ".join(rows[0]) + " |",
                 "|" + "|".join([" --- "] * width) + "|"]
        lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
        self.out.append("\n".join(lines))

    def result(self):
        self._flush()
        text = "\n\n".join(b for b in self.out if b.strip())
        return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def html_to_markdown(raw):
    p = _LatexmlToMarkdown()
    p.feed(raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw)
    return p.result()


def looks_like_latexml(raw):
    """arXiv answers 200 with a placeholder page for papers it could not render."""
    head = raw[:200000].decode("utf-8", errors="replace")
    return "ltx_document" in head or "ltx_page_main" in head


# --------------------------------------------------------------------- fetch


def fetch_one(root, ident, want_source=False, force=False, resolver=None):
    """Run the ladder for one identifier. Returns the record dict."""
    papers = root / "papers"
    papers.mkdir(parents=True, exist_ok=True)
    (papers / "_fetch").mkdir(exist_ok=True)

    arx = norm_arxiv(ident)
    doi = None
    title = None
    oa_pdf = None

    # A non-arXiv identifier has to be resolved before the ladder can start.
    if not arx and resolver:
        rec = resolver(ident)
        if rec:
            title = rec.get("title")
            arx = norm_arxiv(rec.get("arxivId"))
            doi = rec.get("doi")
            oa_pdf = rec.get("openAccessPdf") or None

    key = arx or (slugify(doi) if doi else slugify(str(ident)))
    # `papers/` is named `<ShortName>-<arxivId>.pdf` by hand and has been since
    # the first read. Adopt an existing stem rather than dropping a second copy
    # of the same paper under a bare id.
    existing = sorted(p for p in papers.glob(f"*{key}.*") if p.is_file())
    if existing:
        stem = re.sub(r"\.[^.]+$", "", existing[0].name)
    else:
        stem = f"{slugify(title, 32)}-{key}" if title else key
    record = {
        "tool": "litfetch",
        "tool_version": TOOL_VERSION,
        "retrieved_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "requested": str(ident),
        "arxivId": arx,
        "doi": doi,
        "title": title,
        "attempts": [],
        "artifacts": [],
        "source_status": "not-found",
        "source_version": None,
    }

    def save(name, data, kind, url, status):
        path = papers / name
        if path.exists() and not force:
            record["attempts"].append(
                {"url": url, "http": status, "kind": kind, "skipped": "exists"})
            record["artifacts"].append({
                "kind": kind, "path": str(path.relative_to(root)),
                "bytes": path.stat().st_size, "sha256": _sha256(path.read_bytes()),
                "url": url, "reused": True})
            return path
        path.write_bytes(data if isinstance(data, bytes) else data.encode())
        record["artifacts"].append({
            "kind": kind, "path": str(path.relative_to(root)),
            "bytes": len(data), "sha256": _sha256(
                data if isinstance(data, bytes) else data.encode()),
            "url": url, "reused": False})
        return path

    if arx:
        # 1. LaTeXML HTML -> markdown
        url = ARXIV_HTML.format(id=arx)
        st, body, ctype, final = _get(url, accept="text/html")
        record["attempts"].append({"url": url, "http": st, "kind": "html",
                                   "bytes": len(body), "content_type": ctype})
        if st == 200 and body and looks_like_latexml(body):
            md = html_to_markdown(body)
            if not title:
                # The paper names itself in its own first h1; use it so a new
                # paper lands under a readable filename like the ones already here.
                m = re.search(r"^#\s+(.+?)\s*(?:<!--|$)", md, re.M)
                if m and not existing:
                    title = m.group(1)
                    record["title"] = title
                    stem = f"{slugify(title, 32)}-{key}"
            save(f"{stem}.html", body, "latexml-html", final, st)
            save(f"{stem}.md", md, "markdown", final, st)
            record["source_status"] = "obtained"
            record["source_version"] = f"arXiv HTML (LaTeXML) {arx}"
        elif st == 200:
            record["attempts"][-1]["note"] = "200 but not a LaTeXML render"

        # 2. PDF alongside, for a visual read of the figures
        url = ARXIV_PDF.format(id=arx)
        st, body, ctype, final = _get(url, accept="application/pdf")
        record["attempts"].append({"url": url, "http": st, "kind": "pdf",
                                   "bytes": len(body), "content_type": ctype})
        if st == 200 and body[:5] == b"%PDF-":
            pdf_path = save(f"{stem}.pdf", body, "pdf", final, st)
            if record["source_status"] != "obtained":
                record["source_status"] = "obtained"
                record["source_version"] = f"arXiv PDF {arx}"

        # 3. LaTeX source, on request
        if want_source:
            (papers / "_src").mkdir(exist_ok=True)
            url = ARXIV_SRC.format(id=arx)
            st, body, ctype, final = _get(url)
            record["attempts"].append({"url": url, "http": st, "kind": "eprint",
                                       "bytes": len(body), "content_type": ctype})
            if st == 200 and body:
                save(f"_src/{arx}.tar.gz", body, "latex-source", final, st)
    else:
        # 4. Non-arXiv: PDF only, and say so plainly.
        for url in [u for u in (oa_pdf, f"https://doi.org/{doi}" if doi else None) if u]:
            st, body, ctype, final = _get(url, accept="application/pdf")
            record["attempts"].append({"url": url, "http": st, "kind": "pdf",
                                       "bytes": len(body), "content_type": ctype})
            if st == 200 and body[:5] == b"%PDF-":
                pdf_path = save(f"{stem}.pdf", body, "pdf", final, st)
                record["source_status"] = "obtained"
                record["source_version"] = f"publisher PDF via {final}"
                break
        else:
            record["source_status"] = "paywalled" if (oa_pdf or doi) else "not-found"

    out = papers / "_fetch" / f"{key}.json"
    out.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    record["_record_path"] = str(out.relative_to(root))
    return record


def render(record):
    lines = [f"  {record['requested']} -> {record['source_status']}"]
    for a in record["artifacts"]:
        if a.get("status") in ("skipped", "failed"):
            lines.append(f"    [{a['status']:5}] {a['kind']:14} {a.get('reason', '')}")
            continue
        mark = "reused" if a.get("reused") else "new"
        extra = f"  images:{a['images']}" if a.get("images") else ""
        lines.append(f"    [{mark}] {a['kind']:14} {a['path']}  "
                     f"{a['bytes']:>9,} B  sha256:{a['sha256'][:12]}{extra}")
    for at in record["attempts"]:
        if at.get("http") != 200:
            lines.append(f"    [miss ] {at['kind']:14} HTTP {at['http']}  {at['url']}")
    return "\n".join(lines)
