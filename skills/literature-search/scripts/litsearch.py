#!/usr/bin/env python3
"""Standalone deterministic and replayable literature search.

The tool queries Semantic Scholar and arXiv without an LLM. Search result sets,
screening decisions, fetched papers, and throttle state live under a configurable
workspace, which defaults to `.litsearch` in the current project.

Backends
  Semantic Scholar Graph API   (needs S2_API_KEY)
  Semantic Scholar Recommendations API
  arXiv Atom API               (no key)

Channels
  recent    newest matching papers
  search    fuzzy relevance recall
  rec       neighbors of seed papers
  snippets  matching full-text excerpts
  arxiv     fresh arXiv submissions
  cites     papers citing a known paper
  refs      references used by a known paper
  paper     resolve persistent identifiers
  audit     recent + search + recommendations + arXiv
  fetch     arXiv HTML/Markdown/PDF or an open publisher PDF, without MinerU
  screen    persistent metadata-level triage decisions
"""

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import litfetch          # full-text acquisition ladder; see its module docstring
import litscreen        # the screening ledger: what we saw, and what we decided

TOOL_VERSION = "2.0.0"
S2_GRAPH = "https://api.semanticscholar.org/graph/v1"
S2_REC = "https://api.semanticscholar.org/recommendations/v1"
ARXIV = "https://export.arxiv.org/api/query"

# Measured 2026-09-03: with a valid key, back-to-back calls 1.1s apart still 429
# intermittently, and separate endpoints share the pool. Serialize and back off.
MIN_INTERVAL = 1.3
MAX_RETRIES = 6

# The pool is shared across processes, not just across calls within one. Before
# this file existed, every invocation started believing the pool was idle, so the
# first call of each audit channel spent a 2 s retry discovering otherwise - an
# audit fires 20+ calls, and a sweep of them paid that toll repeatedly. The
# timestamp is advisory: a stale or unreadable file simply costs one retry, which
# is the behaviour we had anyway.
THROTTLE_STATE = ("state", "litsearch-throttle.json")

PAPER_FIELDS = (
    "paperId,externalIds,title,abstract,tldr,venue,year,publicationDate,"
    "citationCount,influentialCitationCount,authors,openAccessPdf"
)
# Verified 2026-09-03: /paper/search/bulk, /recommendations/*, and
# /paper/{id}/citations all reject `tldr` with HTTP 400
# "Unrecognized or unsupported fields: [tldr]". Only /paper/search and
# /paper/batch accept it. Keep a reduced set for the rest.
CORE_FIELDS = (
    "paperId,externalIds,title,abstract,venue,year,publicationDate,"
    "citationCount,influentialCitationCount,authors,openAccessPdf"
)

_last_call = [0.0]
_active_root: list[pathlib.Path | None] = [None]


def _root():
    if _active_root[0] is not None:
        return _active_root[0]
    configured = os.environ.get("LITSEARCH_WORKSPACE")
    if configured:
        return pathlib.Path(configured).expanduser().resolve()
    return (pathlib.Path.cwd() / ".litsearch").resolve()


def _throttle_path():
    try:
        p = _root().joinpath(*THROTTLE_STATE)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    except Exception:
        return None


def _throttle():
    """Serialize calls to 1 per MIN_INTERVAL, across processes as well as within one."""
    if _last_call[0] == 0.0:
        p = _throttle_path()
        if p and p.exists():
            try:
                _last_call[0] = float(json.loads(p.read_text()).get("last_call", 0.0))
            except (ValueError, OSError):
                pass
    wait = MIN_INTERVAL - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.time()
    p = _throttle_path()
    if p:
        try:
            p.write_text(json.dumps({"last_call": _last_call[0],
                                     "min_interval": MIN_INTERVAL}))
        except OSError:
            pass


def _request(url, params=None, payload=None, headers=None, label=""):
    """One HTTP call with 1 rps throttling and exponential backoff on 429/5xx."""
    if params:
        url = url + "?" + urllib.parse.urlencode(params, doseq=True)
    hdrs = {"User-Agent": f"litsearch/{TOOL_VERSION}"}
    hdrs.update(headers or {})
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        hdrs["Content-Type"] = "application/json"

    delay = 2.0
    for attempt in range(1, MAX_RETRIES + 1):
        _throttle()
        req = urllib.request.Request(url, data=data, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=60) as rsp:
                return json.loads(rsp.read().decode()), url
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:200]
            if e.code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES:
                sys.stderr.write(
                    f"  [{label}] HTTP {e.code}, retry {attempt}/{MAX_RETRIES} in {delay:.0f}s\n"
                )
                time.sleep(delay)
                delay *= 2
                continue
            raise SystemExit(f"litsearch: {label} failed HTTP {e.code}: {body}")
        except urllib.error.URLError as e:
            if attempt < MAX_RETRIES:
                sys.stderr.write(f"  [{label}] {e.reason}, retry {attempt}/{MAX_RETRIES}\n")
                time.sleep(delay)
                delay *= 2
                continue
            raise SystemExit(f"litsearch: {label} network error: {e.reason}")
    raise SystemExit(f"litsearch: {label} exhausted retries")


def _s2_headers():
    key = os.environ.get("S2_API_KEY")
    if not key:
        sys.stderr.write(
            "litsearch: S2_API_KEY is not set. The unauthenticated Semantic Scholar\n"
            "           pool returns 429 immediately; expect this to fail.\n"
        )
        return {}
    return {"x-api-key": key}


def _xml_text(node, tag, ns):
    el = node.find(tag, ns)
    return (el.text or "").strip() if el is not None and el.text else None


# ---------------------------------------------------------------- normalisation

def _norm_title(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def _key(rec):
    """Dedupe key, strongest identifier first."""
    ext = rec.get("externalIds") or {}
    for k in ("ArXiv", "DOI", "CorpusId"):
        if ext.get(k):
            return f"{k}:{ext[k]}".lower()
    if rec.get("paperId"):
        return f"s2:{rec['paperId']}"
    return "title:" + _norm_title(rec.get("title"))


# --------------------------------------------------------------- paper notes

READ_DEPTHS = ("metadata", "abstract", "targeted", "full", "full+code")
SOURCE_STATUSES = ("not-attempted", "obtained", "paywalled", "not-found",
                   "retrieval-failed")


def _norm_arxiv(x):
    if not x:
        return None
    x = re.sub(r"^arxiv[:/]\s*", "", str(x).strip().lower())
    x = re.sub(r"^https?://arxiv\.org/(abs|pdf)/", "", x)
    x = re.sub(r"\.pdf$", "", x)
    x = re.sub(r"v\d+$", "", x)
    return x or None


def _norm_doi(x):
    if not x:
        return None
    x = str(x).strip().lower()
    x = re.sub(r"^https?://(dx\.)?doi\.org/", "", x)
    x = re.sub(r"^doi[:/]\s*", "", x)
    return x or None


def _frontmatter(path):
    """Minimal YAML-ish frontmatter reader. Same shape researchctl writes."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    if not lines or lines[0].strip() != "---":
        return {}
    out = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip().strip('"')
    return out


def load_paper_notes(root):
    """Derive the paper-note index from the notes themselves, at call time.

    Returns (index, notes). `index` maps a strong external id to a note summary;
    title keys are kept separately as a weak fallback because two different papers
    can share a title and a false merge is worse than a miss.
    """
    index, notes, warnings = {}, [], []
    d = root / "notes"
    if not d.exists():
        return index, notes, warnings
    for path in sorted(d.glob("*.md")):
        fm = _frontmatter(path)
        if fm.get("subtype") != "paper-note":
            continue
        depth = (fm.get("read_depth") or "").strip()
        status = (fm.get("source_status") or "").strip()
        rel = str(path.relative_to(root))
        if depth not in READ_DEPTHS:
            warnings.append(f"{rel}: read_depth={depth!r} not in {READ_DEPTHS}")
        if status not in SOURCE_STATUSES:
            warnings.append(f"{rel}: source_status={status!r} not in {SOURCE_STATUSES}")
        summary = {
            "id": fm.get("id"),
            "path": rel,
            "title": fm.get("title"),
            "paper_title": fm.get("paper_title") or None,
            "read_depth": depth or None,
            "source_status": status or None,
            "source_version": fm.get("source_version") or None,
            "review_status": fm.get("review_status") or None,
            "read_date": fm.get("read_date") or None,
        }
        notes.append(summary)
        for key in filter(None, (
            _norm_arxiv(fm.get("arxiv_id")) and "arxiv:" + _norm_arxiv(fm.get("arxiv_id")),
            _norm_doi(fm.get("doi")) and "doi:" + _norm_doi(fm.get("doi")),
            (fm.get("s2_paper_id") or "").strip().lower() and "s2:" + fm["s2_paper_id"].strip().lower(),
            _norm_title(fm.get("paper_title")) and "title:" + _norm_title(fm.get("paper_title")),
            # The note's own title is a description ("LASA deep read"), not necessarily
            # the paper's title, so it is only a last-resort key.
            _norm_title(fm.get("title")) and "title:" + _norm_title(fm.get("title")),
        )):
            prev = index.get(key)
            if prev and prev["path"] != rel:
                warnings.append(
                    f"{key}: claimed by two notes, {prev['path']} and {rel}")
                continue
            index[key] = summary
    return index, notes, warnings


def annotate_notes(records, index):
    """Attach the matching paper note to each record, in place.

    `matched_by` records which identifier made the join, so a title-only match is
    visibly weaker evidence than an arXiv or DOI match.
    """
    hits = 0
    for r in records:
        candidates = [
            ("arxiv", _norm_arxiv(r.get("arxivId")) and "arxiv:" + _norm_arxiv(r.get("arxivId"))),
            ("doi", _norm_doi(r.get("doi")) and "doi:" + _norm_doi(r.get("doi"))),
            ("s2", (r.get("paperId") or "").strip().lower() and "s2:" + str(r["paperId"]).strip().lower()),
            ("title", _norm_title(r.get("title")) and "title:" + _norm_title(r.get("title"))),
        ]
        note = None
        for how, key in candidates:
            if key and key in index:
                note = dict(index[key], matched_by=how)
                break
        r["note"] = note
        hits += bool(note)
    return hits


def _s2_norm(p, channel):
    ext = p.get("externalIds") or {}
    tldr = p.get("tldr") or {}
    return {
        "source": "semanticscholar",
        "channel": channel,
        "paperId": p.get("paperId"),
        "title": p.get("title"),
        "authors": [a.get("name") for a in (p.get("authors") or [])][:12],
        "venue": p.get("venue"),
        "year": p.get("year"),
        "publicationDate": p.get("publicationDate"),
        "citationCount": p.get("citationCount"),
        "influentialCitationCount": p.get("influentialCitationCount"),
        "externalIds": ext,
        "arxivId": ext.get("ArXiv"),
        "doi": ext.get("DOI"),
        "url": (
            f"https://arxiv.org/abs/{ext['ArXiv']}" if ext.get("ArXiv")
            else f"https://doi.org/{ext['DOI']}" if ext.get("DOI")
            else f"https://www.semanticscholar.org/paper/{p.get('paperId')}"
        ),
        "openAccessPdf": (p.get("openAccessPdf") or {}).get("url") or None,
        "tldr": tldr.get("text"),
        "abstract": p.get("abstract"),
    }


def _arxiv_norm(entry, ns):
    aid = (_xml_text(entry, "atom:id", ns) or "").rsplit("/", 1)[-1]
    doi_el = entry.find("arxiv:doi", ns)
    return {
        "source": "arxiv",
        "channel": "arxiv",
        "paperId": None,
        "title": " ".join((_xml_text(entry, "atom:title", ns) or "").split()),
        "authors": [
            (a.find("atom:name", ns).text or "").strip()
            for a in entry.findall("atom:author", ns)
        ][:12],
        "venue": _xml_text(entry, "arxiv:journal_ref", ns),
        # arXiv's free-text comment is where "Accepted to CVPR 2026" appears
        # first, months before DBLP indexes the proceedings or S2 rewrites
        # `venue`. It is also frequently absent on a paper that *was* accepted,
        # so it is one signal of three, never a gate.
        "comment": _xml_text(entry, "arxiv:comment", ns),
        "journal_ref": _xml_text(entry, "arxiv:journal_ref", ns),
        "year": int(( _xml_text(entry, "atom:published", ns) or "0")[:4] or 0) or None,
        "publicationDate": (_xml_text(entry, "atom:published", ns) or "")[:10] or None,
        "updatedDate": (_xml_text(entry, "atom:updated", ns) or "")[:10] or None,
        "citationCount": None,
        "influentialCitationCount": None,
        "externalIds": {"ArXiv": re.sub(r"v\d+$", "", aid)},
        "arxivId": re.sub(r"v\d+$", "", aid),
        "doi": doi_el.text.strip() if doi_el is not None and doi_el.text else None,
        "url": f"https://arxiv.org/abs/{aid}",
        "openAccessPdf": f"https://arxiv.org/pdf/{aid}",
        "tldr": None,
        "abstract": " ".join((_xml_text(entry, "atom:summary", ns) or "").split()),
    }


# ------------------------------------------------------------------- channels

def ch_recent(query, since=None, limit=200, extra_fields=""):
    """Recency channel. Boolean query over title+abstract, newest first.

    Fixes AI Scientist's citation-count sort, which ranks brand-new preprints
    last - exactly the papers a novelty audit exists to find.
    """
    params = {
        "query": query,
        "sort": "publicationDate:desc",
        "fields": CORE_FIELDS + extra_fields,
    }
    if since:
        params["publicationDateOrYear"] = f"{since}:"
    d, url = _request(f"{S2_GRAPH}/paper/search/bulk", params=params,
                      headers=_s2_headers(), label="recent")
    recs = [_s2_norm(p, "recent") for p in (d.get("data") or [])[:limit]]
    return recs, {"endpoint": f"{S2_GRAPH}/paper/search/bulk", "params": params,
                  "total_matched": d.get("total"), "resolved_url": url}


def ch_search(query, limit=20):
    """Relevance channel. Fuzzy natural-language recall."""
    params = {"query": query, "limit": min(limit, 100), "fields": PAPER_FIELDS}
    d, url = _request(f"{S2_GRAPH}/paper/search", params=params,
                      headers=_s2_headers(), label="search")
    recs = [_s2_norm(p, "search") for p in (d.get("data") or [])]
    if not recs and len(query.split()) > 6:
        sys.stderr.write(
            "litsearch: relevance search matched nothing. This endpoint effectively\n"
            "           ANDs terms, so long sentences return 0. Retry with 3-6 keywords.\n"
        )
    return recs, {"endpoint": f"{S2_GRAPH}/paper/search", "params": params,
                  "total_matched": d.get("total"), "resolved_url": url}


def ch_rec(positive, negative=None, limit=50, corpus="recent"):
    """Recommendation channel.

    The piece AI Scientist has no equivalent of. Given seed papers that describe
    our direction, S2 returns structural neighbours we never thought to query.
    corpus=recent surfaces new low-citation work; corpus=all-cs surfaces the
    established prior art a reviewer will say we should have cited.
    """
    if corpus == "all-cs":
        if len(positive) != 1:
            raise SystemExit("litsearch: corpus=all-cs takes exactly one --positive id")
        params = {"limit": min(limit, 100), "fields": CORE_FIELDS, "from": "all-cs"}
        d, url = _request(f"{S2_REC}/papers/forpaper/{positive[0]}", params=params,
                          headers=_s2_headers(), label="rec")
        meta_ep = f"{S2_REC}/papers/forpaper/{positive[0]}"
        payload = None
    else:
        params = {"limit": min(limit, 100), "fields": CORE_FIELDS}
        payload = {"positivePaperIds": positive, "negativePaperIds": negative or []}
        d, url = _request(f"{S2_REC}/papers", params=params, payload=payload,
                          headers=_s2_headers(), label="rec")
        meta_ep = f"{S2_REC}/papers"
    recs = [_s2_norm(p, "rec") for p in (d.get("recommendedPapers") or [])]
    return recs, {"endpoint": meta_ep, "params": params, "payload": payload,
                  "resolved_url": url}


def ch_snippets(query, limit=10):
    """Full-text channel. Returns body text with its section heading.

    Fixes AI Scientist's abstract-only judgement, the failure mode that made an
    abstract-level reader over-rate two threats in L001.
    """
    params = {"query": query, "limit": min(limit, 100)}
    d, url = _request(f"{S2_GRAPH}/snippet/search", params=params,
                      headers=_s2_headers(), label="snippets")
    out = []
    for it in d.get("data") or []:
        sn = it.get("snippet") or {}
        pp = it.get("paper") or {}
        out.append({
            "source": "semanticscholar",
            "channel": "snippets",
            "paperId": pp.get("corpusId") and f"CorpusId:{pp['corpusId']}" or pp.get("paperId"),
            "title": pp.get("title"),
            "externalIds": pp.get("externalIds") or {},
            "arxivId": (pp.get("externalIds") or {}).get("ArXiv"),
            "doi": (pp.get("externalIds") or {}).get("DOI"),
            "url": pp.get("openAccessInfo", {}).get("url") if isinstance(pp.get("openAccessInfo"), dict) else None,
            "snippetKind": sn.get("snippetKind"),
            "section": sn.get("section"),
            "text": sn.get("text"),
            "score": it.get("score"),
        })
    return out, {"endpoint": f"{S2_GRAPH}/snippet/search", "params": params,
                 "resolved_url": url}


def _arxiv_query(query):
    """Build an arXiv search_query.

    A bare multi-word query must become an AND of terms. Wrapping the whole
    string in quotes turns it into an exact-phrase match and returns nothing.
    Double-quoted spans in the input are preserved as phrases; a query that
    already uses arXiv field prefixes (all:, ti:, abs:, cat:) is passed through.
    """
    if re.search(r"\b(all|ti|abs|au|cat|co|jr|rn|id):", query):
        return query
    parts = re.findall(r'"([^"]+)"|(\S+)', query)
    terms = []
    for phrase, word in parts:
        if phrase:
            terms.append(f'all:"{phrase}"')
        elif word:
            terms.append(f"all:{word}")
    return " AND ".join(terms) if terms else f"all:{query}"


def ch_arxiv(query, limit=50, category=None):
    """arXiv channel. Newest submissions first, no key, no S2 indexing lag."""
    q = _arxiv_query(query)
    if category:
        q = f"({q}) AND cat:{category}"
    params = {
        "search_query": q,
        "max_results": min(limit, 200),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    url = ARXIV + "?" + urllib.parse.urlencode(params)
    for attempt in range(1, MAX_RETRIES + 1):
        _throttle()
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": f"litsearch/{TOOL_VERSION}"}),
                timeout=60,
            ) as rsp:
                raw = rsp.read().decode()
            break
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            if attempt == MAX_RETRIES:
                raise SystemExit(f"litsearch: arxiv failed: {e}")
            time.sleep(2 ** attempt)
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
    }
    root = ET.fromstring(raw)
    recs = [_arxiv_norm(e, ns) for e in root.findall("atom:entry", ns)]
    if not recs and len(query.split()) > 5:
        sys.stderr.write(
            "litsearch: arXiv matched nothing. Terms are ANDed, so long queries\n"
            "           return 0. Retry with 3-5 keywords, or pass an explicit\n"
            "           search_query using arXiv field prefixes (all:, ti:, abs:, cat:).\n"
        )
    return recs, {"endpoint": ARXIV, "params": params, "resolved_url": url}


def ch_paper(ids):
    """Resolve ids (ArXiv:xxx, DOI:xxx, CorpusId:xxx, or S2 hash) to records."""
    params = {"fields": PAPER_FIELDS}
    d, url = _request(f"{S2_GRAPH}/paper/batch", params=params, payload={"ids": ids},
                      headers=_s2_headers(), label="paper")
    recs = [_s2_norm(p, "paper") for p in d if p]
    return recs, {"endpoint": f"{S2_GRAPH}/paper/batch", "params": params,
                  "payload": {"ids": ids}, "resolved_url": url}


def ch_cites(paper_id, limit=100):
    """Forward citation sweep: who has cited a known threat since we last looked."""
    params = {"limit": min(limit, 1000), "fields": CORE_FIELDS}
    d, url = _request(f"{S2_GRAPH}/paper/{paper_id}/citations", params=params,
                      headers=_s2_headers(), label="cites")
    recs = [_s2_norm(it.get("citingPaper") or {}, "cites") for it in (d.get("data") or [])]
    recs = [r for r in recs if r.get("title")]
    recs.sort(key=lambda r: r.get("publicationDate") or "", reverse=True)
    return recs, {"endpoint": f"{S2_GRAPH}/paper/{paper_id}/citations", "params": params,
                  "resolved_url": url}


def ch_refs(paper_id, limit=200):
    """Backward citation chaining: what a paper itself builds on.

    The freeze checklist wants both directions. `cites` answers "who came after
    us"; this answers "whose shoulders is the closest work standing on", which is
    where a mechanism that predates the current terminology usually hides.
    """
    params = {"limit": min(limit, 1000), "fields": CORE_FIELDS}
    d, url = _request(f"{S2_GRAPH}/paper/{paper_id}/references", params=params,
                      headers=_s2_headers(), label="refs")
    recs = [_s2_norm(it.get("citedPaper") or {}, "refs") for it in (d.get("data") or [])]
    recs = [r for r in recs if r.get("title")]
    recs.sort(key=lambda r: r.get("publicationDate") or "", reverse=True)
    return recs, {"endpoint": f"{S2_GRAPH}/paper/{paper_id}/references", "params": params,
                  "resolved_url": url}


# ----------------------------------------------------------------- persistence

def _slug(s, n=48):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s.lower())).strip("-")[:n] or "query"


def _write(root, channel, label, records, meta, args_repr):
    outdir = root / "searches"
    outdir.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc)
    path = outdir / f"{now:%Y-%m-%d}-{_slug(label)}-{channel}.json"
    doc = {
        "tool": "litsearch",
        "tool_version": TOOL_VERSION,
        "channel": channel,
        "retrieved_utc": now.isoformat(timespec="seconds"),
        "invocation": args_repr,
        "request": meta,
        "result_count": len(records),
        "results": records,
    }
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    return path


def _note_line(r):
    """One line telling the reader we already have a note on this paper.

    Printed only on a hit. Deliberately carries no verdict: a verdict belongs to
    the dated L audit that made it, against the T/H it was made for, not to the
    paper. Read depth and source status are properties of the paper and travel.
    """
    n = r.get("note")
    if not n:
        return ""
    bits = [f"NOTE {n.get('id') or '?'}"]
    bits.append(f"read={n.get('read_depth') or '?'}")
    bits.append(f"source={n.get('source_status') or '?'}")
    if n.get("source_version"):
        bits.append(f"ver={n['source_version']}")
    if n.get("review_status"):
        bits.append(n["review_status"])
    if n.get("matched_by") == "title":
        bits.append("MATCHED BY TITLE ONLY - confirm identity")
    return "\n     " + " | ".join(bits) + f"\n     -> {n.get('path')}"


def _render(records, channel):
    if not records:
        return "  (no results)"
    lines = []
    for i, r in enumerate(records, 1):
        screen = litscreen.screen_line(r)
        screen = ("\n" + screen) if screen else ""
        if channel == "snippets":
            lines.append(
                f"{i:>3}. {r.get('title')}\n"
                f"     [{r.get('snippetKind')} | {r.get('section')}] score={r.get('score')}\n"
                f"     {(r.get('text') or '')[:400]}" + _note_line(r) + screen
            )
            continue
        ident = (f"arXiv:{r['arxivId']}" if r.get("arxivId")
                 else f"doi:{r['doi']}" if r.get("doi")
                 else str(r.get("paperId") or "?")[:12])
        cc = r.get("citationCount")
        lines.append(
            f"{i:>3}. [{r.get('publicationDate') or r.get('year') or '?'}] {r.get('title')}\n"
            f"     {ident} | cites={cc if cc is not None else 'n/a'} | {r.get('venue') or ''}\n"
            f"     {(r.get('tldr') or r.get('abstract') or '')[:260]}" + _note_line(r) + screen
        )
    return "\n".join(lines)


def _dedupe(groups):
    """Merge channel result lists, first occurrence wins, record every channel hit."""
    merged = {}
    for channel, recs in groups:
        for r in recs:
            k = _key(r)
            if k in merged:
                merged[k].setdefault("found_by", []).append(channel)
            else:
                r = dict(r)
                r["found_by"] = [channel]
                merged[k] = r
    out = list(merged.values())
    out.sort(key=lambda r: (r.get("publicationDate") or str(r.get("year") or "")), reverse=True)
    return out


# --------------------------------------------------------------- subcommands

def _cmd_fetch(root, args):
    """Acquire full text. See litfetch's docstring for why the ladder is ordered so."""
    def resolver(ident):
        """Only reached for a non-arXiv identifier; costs one S2 call."""
        try:
            recs, _ = ch_paper([ident])
        except SystemExit as e:
            sys.stderr.write(f"litfetch: could not resolve {ident}: {e}\n")
            return None
        return recs[0] if recs else None

    print(f"\n=== litfetch: {len(args.ids)} identifier(s) ===")
    got = 0
    for ident in args.ids:
        rec = litfetch.fetch_one(root, ident, want_source=args.source,
                                 force=args.force, resolver=resolver)
        print(litfetch.render(rec))
        print(f"    record: {rec['_record_path']}")
        if rec["source_status"] == "obtained":
            got += 1

    print(f"\n{got}/{len(args.ids)} obtained.")
    print("A paper note's read_depth is still a claim about what you read. `obtained`\n"
          "means the file is on disk, not that anybody has read it.")
    return 0


def _cmd_screen(root, args):
    if args.op == "sync":
        entries, n_files, added, bad, migrated = litscreen.sync(root, _key)
        path = litscreen.save(root, entries)
        for b in bad:
            sys.stderr.write(f"litscreen: {b}\n")
        undecided = sum(1 for e in entries.values()
                        if (e.get("disposition") or "unscreened") == "unscreened")
        print(f"\n=== litscreen sync ===")
        print(f"  {n_files} result set(s) scanned")
        print(f"  {len(entries)} paper(s) in the ledger ({added} new)")
        if migrated:
            print(f"  {migrated} decision(s) migrated onto a merged key")
        print(f"  {undecided} unscreened")
        print(f"  ledger: {path.relative_to(root)}")
        return 0

    entries, bad = litscreen.load(root)
    for b in bad:
        sys.stderr.write(f"litscreen: {b}\n")

    if args.op == "set":
        if not args.ident:
            sys.stderr.write("litscreen: `set` needs an identifier\n")
            return 2
        key = litscreen.resolve(entries, args.ident)
        if key is None:
            sys.stderr.write(
                f"litscreen: {args.ident} is not in the ledger. If it came from a new\n"
                f"           sweep, run `litsearch screen sync` first.\n")
            return 1
        e = litscreen.set_decision(entries, key, args.score, args.disposition,
                                   args.comment, args.audit)
        litscreen.save(root, entries)
        print(f"\n  {key}  {e.get('title')}")
        print(f"  disposition={e.get('disposition')} score={e.get('score')} "
              f"audit={e.get('decided_in')}")
        if e.get("history"):
            print(f"  {len(e['history'])} previous decision(s) kept in history")
        return 0

    # list
    rows = list(entries.values())
    if args.undecided:
        rows = [e for e in rows if (e.get("disposition") or "unscreened") == "unscreened"]
    if args.min_score is not None:
        rows = [e for e in rows if (e.get("score") or -99) >= args.min_score]
    rows.sort(key=lambda e: (-(e.get("score") if e.get("score") is not None else -1),
                             -(e.get("times_seen") or 0),
                             e.get("first_seen") or ""))
    print(f"\n=== litscreen: {len(rows)} of {len(entries)} paper(s) ===")
    for e in rows[:args.limit]:
        ident = (f"arXiv:{e['arxivId']}" if e.get("arxivId")
                 else f"doi:{e['doi']}" if e.get("doi") else (e.get("key") or "?")[:16])
        score = e.get("score")
        print(f"  [{e.get('disposition') or 'unscreened':10}] "
              f"score={'-' if score is None else score:>3} "
              f"seen={e.get('times_seen'):>2}x  {ident}")
        print(f"       {(e.get('title') or '')[:96]}")
        if e.get("comment"):
            print(f"       ^ {e['comment']}")
    if len(rows) > args.limit:
        print(f"\n  ... {len(rows) - args.limit} more (raise --limit)")
    return 0


# ------------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(
        prog="litsearch",
        description="Deterministic, replayable literature search (Semantic Scholar + arXiv).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    # Shared flags, attached to both the top level and every subcommand, so that
    # `litsearch audit ... --no-write` works as naturally as `litsearch --no-write audit ...`.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--no-write", action="store_true",
                        help="print only, do not persist a result set")
    common.add_argument("--json", action="store_true",
                        help="print raw JSON instead of the readable block")
    common.add_argument(
        "--workspace",
        default=os.environ.get("LITSEARCH_WORKSPACE", ".litsearch"),
        help="directory for searches, screening, notes, fetched papers, and state",
    )
    for a in common._actions:
        ap._add_action(a)
    sub = ap.add_subparsers(dest="cmd", required=True, parser_class=lambda **kw: argparse.ArgumentParser(parents=[common], **kw))

    p = sub.add_parser("recent", help="newest-first boolean search (contemporaneous preprints)")
    p.add_argument("query")
    p.add_argument("--since", help="YYYY-MM-DD lower bound on publication date")
    p.add_argument("--limit", type=int, default=200)

    p = sub.add_parser("search", help="relevance search (fuzzy recall)")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=20)

    p = sub.add_parser("rec", help="recommendations from seed papers")
    p.add_argument("--positive", nargs="+", required=True, metavar="ID")
    p.add_argument("--negative", nargs="*", default=[], metavar="ID")
    p.add_argument("--corpus", choices=["recent", "all-cs"], default="recent")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--label", default="recommendations")

    p = sub.add_parser("snippets", help="full-text snippet search")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=10)

    p = sub.add_parser("arxiv", help="arXiv, newest submissions first")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--category", help="e.g. cs.RO, cs.LG")

    p = sub.add_parser("paper", help="resolve ids to full records")
    p.add_argument("ids", nargs="+")

    p = sub.add_parser("cites", help="forward citation sweep on one paper")
    p.add_argument("id")
    p.add_argument("--limit", type=int, default=100)

    p = sub.add_parser("fetch", help="acquire full text: arXiv HTML/markdown, PDF, LaTeX source")
    p.add_argument("ids", nargs="+", metavar="ID",
                   help="arXiv id, DOI, S2 id, or a URL containing one")
    p.add_argument("--source", action="store_true",
                   help="also pull the LaTeX e-print tarball (exact table numbers)")
    p.add_argument("--force", action="store_true",
                   help="re-download and overwrite artifacts that already exist")

    p = sub.add_parser("screen", help="the screening ledger: what we saw, and what we decided")
    p.add_argument("op", choices=["sync", "set", "list"],
                   help="sync: rebuild sightings from result sets; "
                        "set: record a triage decision; list: show the ledger")
    p.add_argument("ident", nargs="?", help="set: ledger key, arXiv id, DOI or S2 id")
    p.add_argument("--score", type=int,
                   help="triage score from the protocol's rubric (>=5 means read)")
    p.add_argument("--disposition", choices=litscreen.DISPOSITIONS)
    p.add_argument("--comment", help="one line on why; this is what a later reader reads")
    p.add_argument("--audit", help="the L id this decision was made in")
    p.add_argument("--undecided", action="store_true", help="list: only unscreened papers")
    p.add_argument("--min-score", type=int, help="list: only papers scored at or above this")
    p.add_argument("--limit", type=int, default=40, help="list: cap the output")

    p = sub.add_parser("refs", help="backward citation sweep: what one paper cites")
    p.add_argument("id")
    p.add_argument("--limit", type=int, default=200)

    p = sub.add_parser("audit", help="recent + search + rec + arxiv, deduped into one set")
    p.add_argument("query", help="natural-language description of the idea")
    p.add_argument("--boolean", help="boolean form for the recency channel; defaults to query")
    p.add_argument("--since", help="YYYY-MM-DD lower bound for the recency channel")
    p.add_argument("--seed", nargs="*", default=[], metavar="ID",
                   help="seed paper ids for the recommendation channel")
    p.add_argument("--arxiv", help="dedicated shorter query for the arXiv channel; "
                                   "arXiv ANDs terms, so the full sentence usually returns 0")
    p.add_argument("--category", help="arXiv category filter, e.g. cs.RO")
    p.add_argument("--limit", type=int, default=50, help="per-channel cap")

    args = ap.parse_args()
    root = pathlib.Path(args.workspace).expanduser()
    if not root.is_absolute():
        root = pathlib.Path.cwd() / root
    root = root.resolve()
    _active_root[0] = root
    argv = " ".join(sys.argv[1:])

    # `fetch` and `screen` act on the local workspace rather than querying it.
    if args.cmd == "fetch":
        return _cmd_fetch(root, args)
    if args.cmd == "screen":
        return _cmd_screen(root, args)

    if args.cmd == "audit":
        groups, metas = [], {}
        plan = [
            ("recent", lambda: ch_recent(args.boolean or args.query, args.since, args.limit)),
            ("search", lambda: ch_search(args.query, min(args.limit, 100))),
            ("arxiv", lambda: ch_arxiv(args.arxiv or args.query, args.limit, args.category)),
        ]
        if args.seed:
            plan.append(("rec", lambda: ch_rec(args.seed, [], args.limit, "recent")))
        for name, fn in plan:
            sys.stderr.write(f"litsearch: channel {name} ...\n")
            try:
                recs, meta = fn()
            except SystemExit as e:
                sys.stderr.write(f"  channel {name} failed: {e}\n")
                recs, meta = [], {"error": str(e)}
            groups.append((name, recs))
            metas[name] = {**meta, "result_count": len(recs)}
            sys.stderr.write(f"  {name}: {len(recs)} results\n")
        records = _dedupe(groups)
        meta = {"channels": metas, "dedupe": "arXiv > DOI > CorpusId > s2 id > title"}
        channel, label = "audit", args.query
    else:
        fn = {
            "recent": lambda: ch_recent(args.query, args.since, args.limit),
            "search": lambda: ch_search(args.query, args.limit),
            "rec": lambda: ch_rec(args.positive, args.negative, args.limit, args.corpus),
            "snippets": lambda: ch_snippets(args.query, args.limit),
            "arxiv": lambda: ch_arxiv(args.query, args.limit, args.category),
            "paper": lambda: ch_paper(args.ids),
            "cites": lambda: ch_cites(args.id, args.limit),
            "refs": lambda: ch_refs(args.id, args.limit),
        }[args.cmd]
        records, meta = fn()
        channel = args.cmd
        label = getattr(args, "query", None) or getattr(args, "label", None) \
            or getattr(args, "id", None) or " ".join(getattr(args, "ids", []))

    note_index, all_notes, note_warnings = load_paper_notes(root)
    for w in note_warnings:
        sys.stderr.write(f"litsearch: paper-note warning: {w}\n")
    note_hits = annotate_notes(records, note_index)

    # A paper note says we read it. The ledger says we saw it and what we decided,
    # which is the cheaper question and the one that was previously unanswerable
    # across rounds.
    ledger, ledger_bad = litscreen.load(root)
    for b in ledger_bad:
        sys.stderr.write(f"litsearch: screening-ledger warning: {b}\n")
    screen_hits = litscreen.annotate(records, ledger, _key)

    meta = {
        **meta,
        "paper_notes": {
            "notes_scanned": len(all_notes),
            "keys_indexed": len(note_index),
            "results_with_note": note_hits,
            "warnings": note_warnings,
        },
        "screening_ledger": {
            "ledger_size": len(ledger),
            "results_already_seen": screen_hits,
        },
    }

    if args.json:
        print(json.dumps(records, indent=2, ensure_ascii=False))
    else:
        print(f"\n=== litsearch {channel}: {label} ===")
        print(f"{len(records)} result(s), {note_hits} with an existing paper note "
              f"({len(all_notes)} note(s) on file), {screen_hits} already in the "
              f"screening ledger\n")
        print(_render(records, channel))
        unread = [r for r in records if not r.get("note")]
        if all_notes and unread:
            print(f"\n{len(unread)} result(s) have no paper note. Any strong verdict "
                  f"or baseline choice needs a primary-source read first.")

    if not args.no_write:
        path = _write(root, channel, label, records, meta, argv)
        print(f"\nresult set: {path.relative_to(root)}")


if __name__ == "__main__":
    sys.exit(main() or 0)
