"""arXiv and PubMed search tools."""
import os
import urllib.request
import urllib.parse
import urllib.error
import re
import xml.etree.ElementTree as ET

def arxiv_search(query, max_results=5):
    try:
        max_results = min(int(max_results), 10)
        url = f"http://export.arxiv.org/api/query?search_query=all:{urllib.parse.quote(query)}&start=0&max_results={max_results}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as response:
            xml_data = response.read()
        root = ET.fromstring(xml_data)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall("atom:entry", ns)
        if not entries:
            return "No results found on arXiv."
        
        results = []
        for i, entry in enumerate(entries, 1):
            title = entry.find("atom:title", ns)
            title = title.text.replace("\\n", " ").strip() if title is not None else "No title"
            summary = entry.find("atom:summary", ns)
            summary = summary.text.replace("\\n", " ").strip() if summary is not None else "No summary"
            link = entry.find("atom:id", ns)
            link = link.text if link is not None else ""
            authors = [a.find("atom:name", ns).text for a in entry.findall("atom:author", ns) if a.find("atom:name", ns) is not None]
            results.append(f"[{i}] Title: {title}\\nAuthors: {', '.join(authors)}\\nURL: {link}\\nAbstract: {summary}\\n")
        return "\\n".join(results)
    except Exception as e:
        return f"Error during arXiv search: {e}"

def _pubmed_fetch_raw(query, top_k=10, start_date=None, end_date=None, high_quality_only=True):
    """Return parsed JSON results list from the search API, or a string error."""
    import urllib.request as _req
    import json as _json

    base_url = os.environ.get("PUBMED_SEARCH_URL", "").rstrip("/")
    api_key  = os.environ.get("PUBMED_API_KEY") or os.environ.get("MSS_API_KEY", "")
    if not base_url:
        return "Error: no search server configured. Set PUBMED_SEARCH_URL (e.g. in ~/.local/share/ai/env)."
    if not api_key:
        return "Error: no API key found. Set PUBMED_API_KEY (or MSS_API_KEY) environment variable."

    top_k = max(5, min(10, int(top_k)))
    payload = {"query": query, "top_k": top_k, "high_quality_only": bool(high_quality_only)}
    if start_date:
        payload["start_date"] = start_date
    if end_date:
        payload["end_date"] = end_date

    data = _json.dumps(payload).encode("utf-8")
    req = _req.Request(
        f"{base_url}/search",
        data=data,
        headers={"Content-Type": "application/json", "X-API-Key": api_key},
        method="POST",
    )
    try:
        with _req.urlopen(req, timeout=60) as resp:
            body = _json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return f"Error calling PubMed search API: {e}"

    return body


def pubmed_search(query, top_k=10, start_date=None, end_date=None, high_quality_only=True):
    body = _pubmed_fetch_raw(query, top_k, start_date, end_date, high_quality_only)
    if isinstance(body, str):
        return body  # error string

    results = body.get("results", [])
    if not results:
        return f"No results found for query: {query}"

    lines = [
        f"Search: \"{body.get('query', query)}\"  |  "
        f"{body.get('total_results', len(results))} result(s)  |  "
        f"{body.get('search_time_seconds', '?')}s\n"
    ]
    for i, p in enumerate(results, 1):
        doi = p.get("doi", "N/A")
        doi_url = f"https://doi.org/{doi}" if doi and doi != "N/A" else "N/A"
        lines.append(
            f"[{i}] {p.get('title', 'N/A')}\n"
            f"    Authors : {p.get('authors', 'N/A')}\n"
            f"    Journal : {p.get('journal', 'N/A')}  ({p.get('year', '?')})  [{p.get('source', '')}]\n"
            f"    DOI     : {doi}  →  {doi_url}\n"
            f"    Score   : {p.get('score', '?')}\n"
            f"    Abstract: {p.get('abstract', 'N/A')}\n"
        )
    return "\n".join(lines)

def pubmed_research_round(query, known_dois=None, start_date=None, end_date=None):
    """
    Fetch full abstracts from the search API, read them in Python, and return a
    compact structured digest to the main agent. No LLM sub-process — fast (~1.5s).

    The full abstract text is read here; only extracted key sentences go back to
    the main agent, keeping its context clean across multiple rounds.
    """
    body = _pubmed_fetch_raw(query, top_k=10, start_date=start_date, end_date=end_date)
    if isinstance(body, str):
        return body  # error

    results = body.get("results", [])
    elapsed = body.get("search_time_seconds", "?")
    if not results:
        return f"No results for: {query}"

    known = set(known_dois or [])
    new_papers = []
    repeat_dois = []

    for p in results:
        doi = p.get("doi", "N/A") or "N/A"
        abstract = (p.get("abstract", "") or "").strip()

        # Extract leading sentences (usually background + main finding) and trailing
        # sentence (usually conclusion). Full abstract is read; we surface key parts.
        sentences = re.split(r'(?<=[.!?])\s+', abstract)
        if len(sentences) >= 3:
            excerpt = " ".join(sentences[:2]) + " … " + sentences[-1]
        elif sentences:
            excerpt = " ".join(sentences[:3])
        else:
            excerpt = abstract[:300]

        entry = dict(
            doi=doi,
            title=p.get("title", "N/A"),
            authors=p.get("authors", "N/A"),
            journal=p.get("journal", "N/A"),
            year=p.get("year", "?"),
            source=p.get("source", ""),
            score=round(float(p.get("score", 0)), 4),
            excerpt=excerpt,
        )

        if doi != "N/A" and doi in known:
            repeat_dois.append(doi)
        else:
            new_papers.append(entry)

    # Sort new papers chronologically so temporal narrative is immediately readable
    new_papers.sort(key=lambda x: (x["year"] if isinstance(x["year"], int) else 0))

    lines = [
        f'ROUND: "{query}"',
        f'API: {len(results)} results in {elapsed}s | {len(new_papers)} new, {len(repeat_dois)} already seen\n',
    ]

    if new_papers:
        lines.append("PAPERS (new, oldest → newest):")
        for p in new_papers:
            doi_url = f"https://doi.org/{p['doi']}" if p['doi'] != "N/A" else "N/A"
            lines.append(
                f"  {p['year']} [{p['score']}] {p['title']}\n"
                f"    {p['authors']}\n"
                f"    {p['journal']} [{p['source']}]\n"
                f"    DOI: {p['doi']}  →  {doi_url}\n"
                f"    Abstract: {p['excerpt']}\n"
            )

    if repeat_dois:
        lines.append(f"ALREADY SEEN DOIs: {', '.join(repeat_dois)}")

    return "\n".join(lines)
