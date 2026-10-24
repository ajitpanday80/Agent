"""L1 knowledge base, loaded from markdown files in kb/ (one article per file).

File format: a front-matter block between '---' lines (id, title, category, os, keywords, sources, drafted,
reviewed, status), then '## Steps' (numbered, short, voice-friendly), '## Escalate if', and an optional
'## Technician notes' section (command-line or risky fixes: NEVER walked through by phone; goes in the ticket).

KB_REQUIRE_REVIEWED=true excludes articles whose status is not 'reviewed' (use in production after a human review).
"""
import os
import re
import json
import math
import sqlite3

KB_DIR = Path(os.getenv("KB_DIR", Path(__file__).resolve().parent.parent / "kb"))
OS_VALUES = ("windows", "mac", "any")
STOPWORDS = {"a", "an", "the", "or", "and", "of", "to", "not", "cannot", "can", "will", "is", "my", "our", "we",
             "in", "on", "it", "for", "with", "(windows)", "(mac)"}


def _parse(path: Path) -> dict:
    text = path.read_text()
    m = re.match(r"---\n(.*?)\n---\n(.*)", text, re.S)
    if not m:
        raise ValueError(f"{path.name}: missing front matter")
    meta: dict = {"sources": []}
    current = None
    for line in m.group(1).splitlines():
        if line.startswith("  - ") and current == "sources":
            meta["sources"].append(line[4:].strip())
        elif ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            current = k.strip()
            if current != "sources":      # 'sources:' is followed by a '  - url' list
                meta[current] = v.strip()
    sections: dict[str, str] = {}
    name = None
    for line in m.group(2).splitlines():
        if line.startswith("## "):
            name = line[3:].strip().lower()
            sections[name] = ""
        elif name:
            sections[name] += line + "\n"
    steps = [re.sub(r"^\d+\.\s*", "", ln).strip() for ln in sections.get("steps", "").splitlines() if ln.strip()]
    art = {
        "id": meta["id"], "title": meta["title"], "category": meta["category"], "os": meta.get("os", "any"),
        "keywords": [k.strip().lower() for k in meta.get("keywords", "").split(",") if k.strip()],
        "sources": meta["sources"], "status": meta.get("status", "draft"), "reviewed": meta.get("reviewed", "never"),
        "drafted": meta.get("drafted", ""), "steps": steps,
        "escalate_if": sections.get("escalate if", "").strip(),
        "notes": sections.get("technician notes", "").strip(),
    }
    if art["os"] not in OS_VALUES:
        raise ValueError(f"{path.name}: os must be one of {OS_VALUES}")
    if not art["steps"] or not art["keywords"] or not art["sources"]:
        raise ValueError(f"{path.name}: steps, keywords and sources are required")
    return art


def load() -> list[dict]:
    arts = [_parse(p) for p in sorted(KB_DIR.glob("*.md"))]
    ids = [a["id"] for a in arts]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate article ids in kb/")
    return arts


ARTICLES = load()


def _stem(w: str) -> str:
    """Very light stemmer so 'receiving'/'receive', 'deleted'/'delete', 'prints'/'print' match."""
    for suf in ("ing", "ed", "es", "s", "e"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    return w


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9']+", text.lower()))


def _stems(text: str) -> set[str]:
    return {_stem(t) for t in _tokens(text)}


def search(query: str, limit: int = 2, operating_system: str | None = None) -> list[dict]:
    """Keyword search. A keyword hit is required; title overlap only breaks ties.
    operating_system 'windows' or 'mac' keeps only matching and OS-neutral articles."""
    q = query.lower()
    qt = _tokens(q)
    qs = _stems(q)
    require_reviewed = os.getenv("KB_REQUIRE_REVIEWED", "false").lower() == "true"
    scored = []
    for a in ARTICLES:
        if require_reviewed and a["status"] != "reviewed":
            continue
        if operating_system in ("windows", "mac") and a["os"] not in (operating_system, "any"):
            continue
        score = 0
        for k in a["keywords"]:
            if " " in k or "'" in k or "-" in k:
                score += 3 if k in q else 0
            elif k in qt or _stem(k) in qs:
                score += 2
        if score:
            score += len(qt & _tokens(a["title"]) - STOPWORDS)
            scored.append((score, a))
    scored.sort(key=lambda s: -s[0])
    return [a for _, a in scored[:limit]]


def search_index(query: str, conn: sqlite3.Connection, limit: int = 3,
                 company_name: str | None = None) -> list[dict]:
    """Local BM25-style retrieval over the editable knowledge library; results ground voice answers."""
    terms = _tokens(query) - STOPWORDS
    if not terms:
        return []
    if company_name == "*":
        rows = [dict(r) for r in conn.execute("SELECT * FROM kb_articles WHERE enabled=1 ORDER BY updated_at DESC")]
    elif company_name:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM kb_articles WHERE enabled=1 AND (company_name='' OR lower(company_name)=lower(?)) "
            "ORDER BY CASE WHEN lower(company_name)=lower(?) THEN 0 ELSE 1 END,updated_at DESC",
            (company_name, company_name))]
    else:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM kb_articles WHERE enabled=1 AND company_name='' ORDER BY updated_at DESC")]
    if not rows and company_name != "*":
        return search(query, limit)
    corpus = []
    for row in rows:
        try:
            steps = json.loads(row["steps"])
            sources = json.loads(row["sources"])
        except (ValueError, TypeError):
            steps, sources = [], []
        text = " ".join((row["title"], row["category"], row["content"], " ".join(steps)))
        corpus.append((row, steps, sources, _tokens(text)))
    avg_len = max(1, sum(len(doc[3]) for doc in corpus) / len(corpus))
    scored = []
    for row, steps, sources, tokens in corpus:
        matched = terms & tokens
        if not matched:
            continue
        score = 0.0
        for term in matched:
            df = sum(term in doc[3] for doc in corpus)
            idf = math.log(1 + (len(corpus) - df + 0.5) / (df + 0.5))
            tf = sum(1 for t in re.findall(r"[a-z0-9']+", (row["title"] + " " + row["content"]).lower()) if t == term)
            score += idf * (tf * 2.2) / (tf + 1.2 * (0.25 + 0.75 * len(tokens) / avg_len))
        if score > 0:
            if company_name not in (None, "*") and row.get("company_name"):
                score *= 1.25
            scored.append((score, row, steps, sources))
    scored.sort(key=lambda item: (-item[0], item[1]["title"].lower()))
    return [{"id": row["id"], "title": row["title"], "category": row["category"],
             "company_name": row.get("company_name", ""), "content": row["content"], "steps": steps, "sources": sources,
             "score": round(score, 4)} for score, row, steps, sources in scored[:limit]]


def format_for_agent(article: dict) -> str:
    steps = " ".join(f"Step {i}: {s}" for i, s in enumerate(article["steps"], 1))
    if article.get("content"):
        scope = f"For {article['company_name']}. " if article.get("company_name") else ""
        return f"{scope}{article['title']}. {steps or article['content']} Sources: {', '.join(article.get('sources', []))}"
    return f"{article['title']}. {steps} Escalate if: {article['escalate_if']}"
