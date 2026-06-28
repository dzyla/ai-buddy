#!/usr/bin/env python3
"""
MCP server for PubMed semantic search.
Speaks JSON-RPC 2.0 over stdio (MCP transport).

Start manually:
    PUBMED_API_KEY=<key> python3 pubmed_mcp_server.py

Registered automatically via mcp.json — ai_mcp.py starts it as needed.

Environment variables:
    PUBMED_API_KEY    API key for the search server (required)
    MSS_API_KEY       Alias for PUBMED_API_KEY
    PUBMED_SEARCH_URL Base URL of the search server (default: http://152.53.80.217:8080)
"""

import sys
import os
import json
import urllib.request

SEARCH_URL = os.environ.get("PUBMED_SEARCH_URL", "http://152.53.80.217:8080").rstrip("/")

TOOL_SCHEMA = {
    "name": "pubmed_search",
    "description": (
        "Semantic search across 50M+ biomedical abstracts (PubMed, BioRxiv, MedRxiv, arXiv). "
        "Returns titles, authors, journals, DOIs, abstracts, and relevance scores. "
        "Use iteratively: search → digest abstracts → refine query → search again → synthesise. "
        "Always report DOIs so manuscripts can be retrieved."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural-language search query (3–500 chars)."
            },
            "top_k": {
                "type": "integer",
                "description": "Number of results (5–10, default 10).",
                "default": 10
            },
            "start_date": {
                "type": "string",
                "description": "Restrict to papers on or after this date (YYYY-MM-DD). Optional."
            },
            "end_date": {
                "type": "string",
                "description": "Restrict to papers on or before this date (YYYY-MM-DD). Optional."
            },
            "high_quality_only": {
                "type": "boolean",
                "description": "Exclude papers with missing/stub abstracts (default true).",
                "default": True
            }
        },
        "required": ["query"]
    }
}


def _do_search(query, top_k=10, start_date=None, end_date=None, high_quality_only=True):
    api_key = os.environ.get("PUBMED_API_KEY") or os.environ.get("MSS_API_KEY", "")
    if not api_key:
        return "Error: no API key. Set PUBMED_API_KEY environment variable."

    top_k = max(5, min(10, int(top_k)))
    payload = {"query": query, "top_k": top_k, "high_quality_only": bool(high_quality_only)}
    if start_date:
        payload["start_date"] = start_date
    if end_date:
        payload["end_date"] = end_date

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{SEARCH_URL}/search",
        data=data,
        headers={"Content-Type": "application/json", "X-API-Key": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return f"Error calling PubMed search API: {e}"

    results = body.get("results", [])
    if not results:
        return f"No results for: {query}"

    lines = [
        f'Search: "{body.get("query", query)}"  |  '
        f'{body.get("total_results", len(results))} result(s)  |  '
        f'{body.get("search_time_seconds", "?")}s\n'
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


def _send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _send_error(req_id, code, message):
    _send({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


def main():
    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue

        try:
            msg = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        req_id = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params", {})

        # Notifications (no id) — just ignore
        if req_id is None:
            continue

        if method == "initialize":
            _send({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "pubmed-search", "version": "1.0.0"}
                }
            })

        elif method == "tools/list":
            _send({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": [TOOL_SCHEMA]}
            })

        elif method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})

            if tool_name != "pubmed_search":
                _send_error(req_id, -32601, f"Unknown tool: {tool_name}")
                continue

            query = arguments.get("query", "").strip()
            if not query:
                _send_error(req_id, -32602, "Missing required argument: query")
                continue

            result = _do_search(
                query=query,
                top_k=arguments.get("top_k", 10),
                start_date=arguments.get("start_date"),
                end_date=arguments.get("end_date"),
                high_quality_only=arguments.get("high_quality_only", True),
            )
            _send({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": result}],
                    "isError": result.startswith("Error:")
                }
            })

        else:
            _send_error(req_id, -32601, f"Method not found: {method}")


if __name__ == "__main__":
    main()
