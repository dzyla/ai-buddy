#!/usr/bin/env python3
import sys
import os
import json
import urllib.request
import urllib.parse
import subprocess
import tempfile
import traceback

# Optional imports for scientific tasks
try:
    import pandas as pd
except ImportError:
    pd = None

try:
    from pyfamsa import Aligner, Sequence
except ImportError:
    Aligner = None

# Tools schemas
TOOLS = [
    {
        "name": "pdb_parse",
        "description": "Fetch and parse a PDB file from the RCSB server. Extracts chain sequences and basic interactions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pdb_id": {"type": "string", "description": "4-character PDB ID."}
            },
            "required": ["pdb_id"]
        }
    },
    {
        "name": "uniprot_search",
        "description": "Search UniProt database for protein sequences and information.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "UniProt search query (e.g., gene name or accession)."}
            },
            "required": ["query"]
        }
    },
    {
        "name": "align_sequences",
        "description": "Perform multiple sequence alignment using pyfamsa.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sequences": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of protein sequences to align."
                }
            },
            "required": ["sequences"]
        }
    },
    {
        "name": "generate_plot",
        "description": "Generate a scientific plot from a CSV file using matplotlib.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "csv_path": {"type": "string", "description": "Path to the input CSV file."},
                "x_col": {"type": "string", "description": "Column for X axis."},
                "y_col": {"type": "string", "description": "Column for Y axis."},
                "output_path": {"type": "string", "description": "Path to save the plot image."}
            },
            "required": ["csv_path", "x_col", "y_col", "output_path"]
        }
    },
    {
        "name": "data_analysis",
        "description": "Run basic descriptive statistics on a CSV dataset using pandas.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "csv_path": {"type": "string", "description": "Path to the CSV file to analyze."}
            },
            "required": ["csv_path"]
        }
    },
    {
        "name": "generate_dashboard",
        "description": "Generate a Streamlit dashboard script from a given dataset.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "script_path": {"type": "string", "description": "Where to save the streamlit .py script."},
                "title": {"type": "string", "description": "Title of the dashboard."},
                "csv_path": {"type": "string", "description": "Path to the data file."}
            },
            "required": ["script_path", "title", "csv_path"]
        }
    },
    {
        "name": "security_audit",
        "description": "Run a security audit on a codebase using Bandit (for Python).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target_path": {"type": "string", "description": "Directory or file to scan."}
            },
            "required": ["target_path"]
        }
    },
    {
        "name": "test_and_debug",
        "description": "Run tests using pytest on a multi-file repository and extract failing test logs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "description": "Path to the repository."}
            },
            "required": ["repo_path"]
        }
    }
]

def _pdb_parse(args):
    pdb_id = args.get("pdb_id", "").lower()
    if not pdb_id or len(pdb_id) != 4:
        return "Error: Invalid PDB ID."
    try:
        url = f"https://files.rcsb.org/download/{pdb_id.upper()}.pdb"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            lines = resp.read().decode("utf-8").splitlines()
        
        chains = {}
        interactions = []
        for line in lines:
            if line.startswith("SEQRES"):
                parts = line.split()
                chain = parts[2]
                res = parts[4:]
                if chain not in chains:
                    chains[chain] = []
                chains[chain].extend(res)
            elif line.startswith("LINK") or line.startswith("SSBOND"):
                interactions.append(line.strip())
                
        out = [f"PDB {pdb_id.upper()} parsed successfully."]
        out.append("Chains found:")
        for ch, seq in chains.items():
            out.append(f"  Chain {ch}: {' '.join(seq[:10])}... (total {len(seq)} residues)")
        out.append(f"Found {len(interactions)} structural links/interactions.")
        if interactions:
            out.append("Sample interactions:")
            for i in interactions[:5]:
                out.append(f"  {i}")
        return "\n".join(out)
    except Exception as e:
        return f"Error fetching or parsing PDB: {e}"

def _uniprot_search(args):
    query = args.get("query", "")
    try:
        q = urllib.parse.quote(query)
        url = f"https://rest.uniprot.org/uniprotkb/search?query={q}&format=json&size=5"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        
        results = data.get("results", [])
        if not results:
            return "No UniProt results found."
        
        out = []
        for r in results:
            acc = r.get("primaryAccession")
            gene = "N/A"
            if "genes" in r and r["genes"]:
                gene = r["genes"][0].get("geneName", {}).get("value", "N/A")
            seq = r.get("sequence", {}).get("value", "")
            out.append(f"Accession: {acc} | Gene: {gene} | Length: {len(seq)}")
        return "\n".join(out)
    except Exception as e:
        return f"Error searching UniProt: {e}"

def _align_sequences(args):
    seqs = args.get("sequences", [])
    if not seqs:
        return "Error: No sequences provided."
    if Aligner is None:
        return "Error: pyfamsa is not installed. Please pip install pyfamsa."
    try:
        sequences = [Sequence(f"Seq_{i}", s.encode('utf-8')) for i, s in enumerate(seqs)]
        aligner = Aligner()
        alignment = aligner.align(sequences)
        out = ["Sequence Alignment:"]
        for s in alignment:
            out.append(f"{s.name.decode('utf-8')}: {s.sequence.decode('utf-8')}")
        return "\n".join(out)
    except Exception as e:
        return f"Error aligning sequences: {e}"

def _generate_plot(args):
    csv_path = args.get("csv_path")
    x_col = args.get("x_col")
    y_col = args.get("y_col")
    out_path = args.get("output_path")
    if pd is None:
        return "Error: pandas is not installed."
    try:
        import matplotlib.pyplot as plt
        df = pd.read_csv(csv_path)
        plt.figure(figsize=(8, 6))
        plt.plot(df[x_col], df[y_col], marker='o')
        plt.xlabel(x_col)
        plt.ylabel(y_col)
        plt.title(f"{y_col} vs {x_col}")
        plt.grid(True)
        plt.savefig(out_path)
        plt.close()
        return f"Plot successfully saved to {out_path}"
    except Exception as e:
        return f"Error generating plot: {e}"

def _data_analysis(args):
    csv_path = args.get("csv_path")
    if pd is None:
        return "Error: pandas is not installed."
    try:
        df = pd.read_csv(csv_path)
        desc = df.describe().to_string()
        cols = ", ".join(df.columns.tolist())
        return f"Columns: {cols}\n\nStatistics:\n{desc}"
    except Exception as e:
        return f"Error analyzing data: {e}"

def _generate_dashboard(args):
    script_path = args.get("script_path")
    title = args.get("title")
    csv_path = args.get("csv_path")
    try:
        code = f'''import streamlit as st
import pandas as pd

st.title("{title}")
st.write("Dashboard generated automatically.")

try:
    df = pd.read_csv("{csv_path}")
    st.write("### Data Preview", df.head())
    st.write("### Summary Statistics", df.describe())
except Exception as e:
    st.error(f"Could not load data: {{e}}")
'''
        with open(script_path, "w") as f:
            f.write(code)
        return f"Streamlit dashboard generated at {script_path}. Run with `streamlit run {script_path}`"
    except Exception as e:
        return f"Error generating dashboard: {e}"

def _security_audit(args):
    target = args.get("target_path")
    try:
        res = subprocess.run(["bandit", "-r", target], capture_output=True, text=True)
        return res.stdout + res.stderr
    except FileNotFoundError:
        return "Error: Bandit is not installed. Please pip install bandit."
    except Exception as e:
        return f"Error running security audit: {e}"

def _test_and_debug(args):
    repo = args.get("repo_path")
    try:
        res = subprocess.run(["pytest", repo, "-v"], capture_output=True, text=True)
        out = res.stdout
        if res.returncode != 0:
            return f"Tests Failed (Code {res.returncode}):\n{out[-2000:]}"
        return f"All Tests Passed!\n{out[-1000:]}"
    except FileNotFoundError:
        return "Error: pytest is not installed."
    except Exception as e:
        return f"Error running tests: {e}"

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

        if req_id is None:
            continue

        if method == "initialize":
            _send({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "advanced-scientific-mcp", "version": "1.0.0"}
                }
            })
        elif method == "tools/list":
            _send({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": TOOLS}
            })
        elif method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})

            handlers = {
                "pdb_parse": _pdb_parse,
                "uniprot_search": _uniprot_search,
                "align_sequences": _align_sequences,
                "generate_plot": _generate_plot,
                "data_analysis": _data_analysis,
                "generate_dashboard": _generate_dashboard,
                "security_audit": _security_audit,
                "test_and_debug": _test_and_debug
            }

            if tool_name not in handlers:
                _send_error(req_id, -32601, f"Unknown tool: {tool_name}")
                continue

            result = handlers[tool_name](arguments)
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
