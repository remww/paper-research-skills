#!/usr/bin/env python3
"""Persistent screening ledger for standalone litsearch result sets.

Sightings are rebuilt from the workspace's `searches/` JSON files. Human or
agent screening decisions are stored separately and preserve prior decisions in
history when changed.
"""

import datetime as dt
import json
import re

DISPOSITIONS = ("unscreened", "read", "queued", "skip", "watch", "noted")
LEDGER = ("screening", "ledger.jsonl")



# --------------------------------------------------------------- acceptance

# Venue strings Semantic Scholar uses for the preprint servers themselves. A
# paper carrying one of these is not thereby unpublished - S2 simply has not
# ingested a proceedings version yet.
_PREPRINT_VENUES = {
    "arxiv.org", "arxiv", "biorxiv", "biorxiv.org", "medrxiv", "medrxiv.org",
    "ssrn electronic journal", "research square", "openreview", "",
}

# "Accepted to CVPR 2026", "to appear at NeurIPS", "camera-ready for ICLR'26".
_ACCEPT_RE = re.compile(
    r"\b(accepted|acccepted|to\s+appear|camera[-\s]?ready|published)\b"
    r"[^.;]{0,80}?\b("
    r"cvpr|iccv|eccv|neurips|nips|iclr|icml|aaai|ijcai|acl|emnlp|naacl|"
    r"siggraph|corl|rss|icra|iros|wacv|bmvc|mm|acm\s*mm|3dv|miccai|kdd|"
    r"aistats|uai|colm|tpami|tro|ral|ijcv"
    r")\b", re.I)


# DBLP and S2 name the same venue differently ("CVPR" against "Computer Vision
# and Pattern Recognition", "NIPS" against "Neural Information Processing
# Systems"), so a raw count splits one venue across several rows. Canonicalise
# for ranking; the raw string stays in `venue`.
_VENUE_CANON = (
    (("computer vision and pattern recognition", "cvpr"), "CVPR"),
    (("international conference on computer vision", "iccv"), "ICCV"),
    (("european conference on computer vision", "eccv"), "ECCV"),
    (("neural information processing systems", "neurips", "nips"), "NeurIPS"),
    (("international conference on machine learning", "icml"), "ICML"),
    (("international conference on learning representations", "iclr"), "ICLR"),
    (("aaai conference on artificial intelligence", "aaai"), "AAAI"),
    (("international joint conference on artificial intelligence", "ijcai"), "IJCAI"),
    (("association for computational linguistics", "acl"), "ACL"),
    (("empirical methods in natural language processing", "emnlp"), "EMNLP"),
    (("conference on robot learning", "corl"), "CoRL"),
    (("robotics: science and systems", "robotics science and systems", "rss"), "RSS"),
    (("international conference on robotics and automation", "icra"), "ICRA"),
    (("intelligent robots and systems", "iros"), "IROS"),
    (("robotics and automation letters", "ral"), "RA-L"),
    (("winter conference on applications of computer vision", "wacv"), "WACV"),
    (("international conference on 3d vision", "3dv"), "3DV"),
    (("pattern analysis and machine intelligence", "tpami"), "TPAMI"),
    (("international journal of computer vision", "ijcv"), "IJCV"),
    (("intelligent transportation systems", "tits"), "T-ITS"),
)


def canon_venue(v):
    """Map a venue string from either source onto one label, or return it as-is."""
    s = (v or "").strip().lower()
    if not s:
        return None
    for keys, label in _VENUE_CANON:
        for k in keys:
            if k == s or k in s:
                return label
    return (v or "").strip()


def acceptance(rec):
    """Best-effort peer-review signal, with its source named.

    Three channels, deliberately independent of one another, because none is
    complete on its own:

      dblp        an `externalIds.DBLP` key under `conf/<venue>/`. DBLP indexes
                  proceedings, so this appears when the conference version is
                  indexed - often before S2 rewrites `venue`.
      venue       an S2 `venue` that is not a preprint server.
      comment     arXiv's `comment` or `journal_ref` field saying so in prose.

    Returns (venue_or_None, source_or_None).

    **Absence is not evidence of rejection.** A paper accepted three weeks ago
    routinely has none of these: DBLP has not indexed the proceedings, S2 still
    says arXiv.org, and the authors have not pushed a new arXiv version with the
    comment. Screening must therefore treat a hit here as a reason to promote a
    paper and must never treat a miss as a reason to drop one, or it reproduces
    the visibility bias it was built to remove.
    """
    ext = rec.get("externalIds") or {}
    k = ext.get("DBLP") or ""
    # `journals/corr/abs-...` is DBLP's key for the arXiv preprint itself and
    # says nothing about review; every other conf/ or journals/ key does.
    parts = k.split("/")
    if len(parts) > 2 and parts[0] in ("conf", "journals") and not k.startswith("journals/corr/"):
        return canon_venue(parts[1]), "dblp"
    v = (rec.get("venue") or "").strip()
    if v and v.lower() not in _PREPRINT_VENUES:
        return canon_venue(v), "venue"
    for fld in ("comment", "journal_ref"):
        m = _ACCEPT_RE.search(rec.get(fld) or "")
        if m:
            return canon_venue(m.group(2)), "comment"
    return None, None


def _path(root):
    p = root.joinpath(*LEDGER)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load(root):
    """Read the ledger into {key: entry}. A corrupt line is reported, not fatal."""
    path = _path(root)
    entries, bad = {}, []
    if not path.exists():
        return entries, bad
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
            entries[e["key"]] = e
        except (ValueError, KeyError) as exc:
            bad.append(f"line {n}: {exc}")
    return entries, bad


def save(root, entries):
    path = _path(root)
    order = sorted(entries.values(),
                   key=lambda e: (e.get("first_seen") or "", e.get("key") or ""))
    path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in order),
                    encoding="utf-8")
    return path


def sync(root, key_fn):
    """Rebuild the sighting half from every persisted result set.

    Decisions are carried across untouched. This is deliberately a full rebuild
    rather than an incremental append: the result sets are canonical, so a
    ledger that disagrees with them is the thing that is wrong.
    """
    entries, bad = load(root)
    searchdir = root / "searches"
    files = sorted(searchdir.glob("*.json")) if searchdir.exists() else []

    seen = {}
    for f in files:
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except ValueError as exc:
            bad.append(f"{f.name}: {exc}")
            continue
        date = (doc.get("retrieved_utc") or "")[:10] or f.name[:10]
        rel = str(f.relative_to(root))
        for r in doc.get("results") or []:
            k = key_fn(r)
            if not k:
                continue
            s = seen.setdefault(k, {
                "key": k, "title": r.get("title"), "year": r.get("year"),
                "arxivId": r.get("arxivId"), "doi": r.get("doi"),
                "paperId": r.get("paperId"), "citationCount": r.get("citationCount"),
                "venue": r.get("venue"), "publicationDate": r.get("publicationDate"),
                "accepted_at": acceptance(r)[0], "accepted_src": acceptance(r)[1],
                "first_seen": date, "first_seen_in": rel,
                "last_seen": date, "times_seen": 0, "channels": [],
            })
            s["times_seen"] += 1
            if date < s["first_seen"]:
                s["first_seen"], s["first_seen_in"] = date, rel
            s["last_seen"] = max(s["last_seen"], date)
            ch = doc.get("channel") or r.get("channel")
            if ch and ch not in s["channels"]:
                s["channels"].append(ch)
            # Metadata improves as channels differ; keep the richest version.
            for fld in ("title", "year", "arxivId", "doi", "paperId", "citationCount",
                        "venue", "publicationDate"):
                if not s.get(fld) and r.get(fld):
                    s[fld] = r[fld]
            # An acceptance signal from any one channel is enough, and a later
            # sweep can be the one that finds it, so never let a blank overwrite
            # a hit.
            if not s.get("accepted_at"):
                av, asrc = acceptance(r)
                if av:
                    s["accepted_at"], s["accepted_src"] = av, asrc

    seen = _merge_aliases(seen)

    # A sync is a rebuild, so a key that merging retired has to go - but never
    # its decision. Carry any decision on a retired key onto the survivor first,
    # and refuse to overwrite a decision the survivor already carries.
    alias_of = {old: k for k, e in seen.items() for old in (e.get("merged_from") or [])}
    migrated = 0
    for old_key in list(entries):
        if old_key in seen:
            continue
        survivor = alias_of.get(old_key)
        old = entries.pop(old_key)
        decided = (old.get("disposition") or "unscreened") != "unscreened"
        if survivor and decided:
            # Guard against the survivor's OWN decision, which lives in `entries`,
            # not in the freshly built `seen` record.
            held = entries.get(survivor) or {}
            tgt = seen[survivor]
            if (held.get("disposition") or "unscreened") == "unscreened":
                for fld in ("disposition", "score", "decided_utc", "decided_in",
                            "comment", "history"):
                    tgt[fld] = old.get(fld)
                tgt.setdefault("history", []).append(
                    {"note": f"decision migrated from retired key {old_key}"})
                migrated += 1
        elif decided:
            # Nothing to migrate onto: keep it rather than lose a judgement.
            entries[old_key] = old
            old["stale"] = "no longer present in any result set"

    added = 0
    for k, s in seen.items():
        old = entries.get(k)
        if old is None:
            added += 1
            s.setdefault("disposition", "unscreened")
            for fld, default in (("score", None), ("decided_utc", None),
                                 ("decided_in", None), ("comment", None),
                                 ("history", [])):
                s.setdefault(fld, default)
            entries[k] = s
        else:
            old.update(s)          # sightings are derived; decisions are not in `s`
    return entries, len(files), added, bad, migrated


def _norm_title(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def _merge_aliases(seen):
    """Collapse entries that are the same paper reached through different ids.

    litsearch keys a record by its strongest identifier, ArXiv > DOI > CorpusId,
    but not every channel returns `externalIds`. The snippet channel in
    particular hands back records with a CorpusId and no arXiv id, so one paper
    can hold two keys and the ledger then answers "have we seen this" wrongly for
    both. Observed on the first sync: Prune2Drive sat at 17 sightings under
    CorpusId:280686603 and 13 more under arXiv:2508.13305.

    Two entries merge when they share any identifier or an identical normalised
    title. The surviving key is the strongest identifier available, so the ledger
    converges on arXiv ids as later sweeps supply them.
    """
    parent = {k: k for k in seen}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    index = {}
    for k, e in seen.items():
        tokens = [f"arxiv:{e['arxivId']}".lower() if e.get("arxivId") else None,
                  f"doi:{e['doi']}".lower() if e.get("doi") else None,
                  f"s2:{e['paperId']}" if e.get("paperId") else None,
                  f"title:{_norm_title(e.get('title'))}" if e.get("title") else None]
        for t in [t for t in tokens if t]:
            if t in index:
                union(index[t], k)
            else:
                index[t] = k

    groups = {}
    for k in seen:
        groups.setdefault(find(k), []).append(k)

    def strength(e):
        return 0 if e.get("arxivId") else 1 if e.get("doi") else 2 if e.get("paperId") else 3

    merged = {}
    for members in groups.values():
        parts = [seen[k] for k in members]
        best = min(parts, key=strength)
        # Keep the winner's own key rather than recomputing one: it is exactly
        # what litsearch's _key() produced for that record, so a later lookup
        # from a fresh sweep lands on this entry.
        key = best["key"]
        out = dict(best)
        out["times_seen"] = sum(p.get("times_seen", 0) for p in parts)
        out["first_seen"] = min(p["first_seen"] for p in parts)
        out["first_seen_in"] = next(p["first_seen_in"] for p in parts
                                    if p["first_seen"] == out["first_seen"])
        out["last_seen"] = max(p["last_seen"] for p in parts)
        out["channels"] = sorted({c for p in parts for c in p.get("channels", [])})
        for fld in ("title", "year", "arxivId", "doi", "paperId", "citationCount",
                    "venue", "publicationDate", "accepted_at", "accepted_src"):
            if not out.get(fld):
                for p in parts:
                    if p.get(fld):
                        out[fld] = p[fld]
                        break
        if len(parts) > 1:
            out["merged_from"] = sorted(p["key"] for p in parts if p["key"] != key)
        merged[key] = out
    return merged


def set_decision(entries, key, score=None, disposition=None, comment=None, audit=None):
    """Record a triage decision, keeping any previous one in `history`."""
    e = entries.get(key)
    if e is None:
        raise KeyError(key)
    if e.get("disposition") not in (None, "unscreened"):
        e.setdefault("history", []).append({
            "disposition": e.get("disposition"), "score": e.get("score"),
            "decided_utc": e.get("decided_utc"), "decided_in": e.get("decided_in"),
            "comment": e.get("comment"),
        })
    if score is not None:
        e["score"] = score
    if disposition is not None:
        e["disposition"] = disposition
    if comment is not None:
        e["comment"] = comment
    if audit is not None:
        e["decided_in"] = audit
    e["decided_utc"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    return e


def resolve(entries, ident):
    """Accept a ledger key, an arXiv id, a DOI, or an S2 id."""
    if ident in entries:
        return ident
    idx = alias_index(entries)
    if ident in idx:
        return idx[ident]
    want = re.sub(r"^(arxiv:|arXiv:)", "", str(ident).strip(), flags=re.I).lower()
    for k, e in entries.items():
        for fld in ("arxivId", "doi", "paperId"):
            v = e.get(fld)
            if v and str(v).lower() == want:
                return k
    return None


def alias_index(entries):
    """Map every key a merged paper was once known by onto its surviving entry."""
    idx = {}
    for k, e in entries.items():
        idx[k] = k
        for old in e.get("merged_from") or []:
            idx[old] = k
    return idx


def annotate(records, entries, key_fn):
    """Attach ledger state to search results, so a sweep shows its own history."""
    idx = alias_index(entries)
    hits = 0
    for r in records:
        e = entries.get(idx.get(key_fn(r), ""))
        if not e:
            continue
        r["screen"] = {
            "disposition": e.get("disposition"), "score": e.get("score"),
            "times_seen": e.get("times_seen"), "first_seen": e.get("first_seen"),
            "decided_in": e.get("decided_in"),
        }
        hits += 1
    return hits


def screen_line(r):
    """One line for a result the ledger already knows about."""
    s = r.get("screen")
    if not s:
        return None
    d = s.get("disposition") or "unscreened"
    bits = [f"seen {s.get('times_seen')}x since {s.get('first_seen')}", f"screen={d}"]
    if s.get("score") is not None:
        bits.append(f"score={s['score']}")
    if s.get("decided_in"):
        bits.append(s["decided_in"])
    return "     ^ " + " | ".join(bits)
