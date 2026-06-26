#!/usr/bin/env python3
import sys
import os
import json
import subprocess
import urllib.request
import urllib.parse
import re

CONFIG_PATHS = [
    os.path.join(os.getcwd(), "mcp.json"),
    os.path.join(os.getcwd(), "mcp_config.json"),
    os.path.expanduser("~/.config/ai/mcp.json"),
    os.path.expanduser("~/.config/ai/mcp_config.json"),
    os.path.expanduser("~/.gemini/config/mcp_config.json"),
    os.path.expanduser("~/.lmstudio/mcp.json"),
]

def load_config():
    for path in CONFIG_PATHS:
        if os.path.exists(path) and os.path.getsize(path) > 0:
            try:
                with open(path, "r") as f:
                    data = json.load(f)
                    if "mcpServers" in data:
                        return data["mcpServers"]
                    return data
            except Exception as e:
                print(f"Warning: failed to load config from {path}: {e}", file=sys.stderr)
    return {}

def run_jsonrpc(proc, method, params, req_id):
    req = {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": method,
        "params": params
    }
    req_str = json.dumps(req) + "\n"
    proc.stdin.write(req_str)
    proc.stdin.flush()

    while True:
        line = proc.stdout.readline()
        if not line:
            raise Exception("Connection closed by server")
        try:
            resp = json.loads(line)
            if resp.get("id") == req_id:
                return resp
        except json.JSONDecodeError:
            pass

def send_notification(proc, method, params=None):
    req = {
        "jsonrpc": "2.0",
        "method": method
    }
    if params is not None:
        req["params"] = params
    req_str = json.dumps(req) + "\n"
    proc.stdin.write(req_str)
    proc.stdin.flush()

def start_server(cfg):
    cmd = []
    if "command" in cfg:
        cmd.append(cfg["command"])
    if "args" in cfg:
        cmd.extend(cfg["args"])
    
    if not cmd:
        return None

    env = os.environ.copy()
    if "env" in cfg:
        for k, v in cfg["env"].items():
            env[k] = str(v)

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=env,
        bufsize=1
    )
    return proc

def init_server(proc):
    init_params = {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "ai", "version": "1.0"}
    }
    resp = run_jsonrpc(proc, "initialize", init_params, req_id=1)
    send_notification(proc, "notifications/initialized")
    return resp

def list_tools(server_name, cfg):
    proc = start_server(cfg)
    if not proc:
        return []
    try:
        init_server(proc)
        resp = run_jsonrpc(proc, "tools/list", {}, req_id=2)
        tools = resp.get("result", {}).get("tools", [])
        namespaced_tools = []
        for t in tools:
            clean_server = "".join(c if c.isalnum() or c == "_" else "_" for c in server_name)
            t["name"] = f"{clean_server}__{t['name']}"
            namespaced_tools.append(t)
        return namespaced_tools
    except Exception as e:
        print(f"Error listing tools from {server_name}: {e}", file=sys.stderr)
        return []
    finally:
        try:
            proc.terminate()
        except:
            pass

def call_tool(server_name, cfg, tool_name, arguments):
    proc = start_server(cfg)
    if not proc:
        return {"error": "Failed to start server"}
    try:
        init_server(proc)
        resp = run_jsonrpc(proc, "tools/call", {"name": tool_name, "arguments": arguments}, req_id=3)
        return resp.get("result", {})
    except Exception as e:
        return {"error": str(e)}
    finally:
        try:
            proc.terminate()
        except:
            pass

def ddg_lite_search(query):
    try:
        url = "https://lite.duckduckgo.com/lite/"
        data = urllib.parse.urlencode({'q': query}).encode('utf-8')
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            html = response.read().decode('utf-8', errors='ignore')
        
        trs = re.findall(r'<tr.*?>(.*?)</tr>', html, re.DOTALL)
        results = []
        
        i = 0
        while i < len(trs):
            tr = trs[i]
            link_match = re.search(r'(<a[^>]+class=\'result-link\'[^>]*>)(.*?)</a>', tr, re.DOTALL)
            if link_match:
                tag = link_match.group(1)
                text = link_match.group(2)
                href_match = re.search(r'href="([^"]+)"', tag)
                link = href_match.group(1) if href_match else ""
                title = re.sub(r'<[^>]+>', '', text).strip()
                
                snippet = ""
                if i + 1 < len(trs):
                    next_tr = trs[i + 1]
                    snippet_match = re.search(r'<td[^>]+class=\'result-snippet\'[^>]*>(.*?)</td>', next_tr, re.DOTALL)
                    if snippet_match:
                        snippet = re.sub(r'<[^>]+>', '', snippet_match.group(1)).strip()
                        snippet = snippet.replace('&amp;', '&').replace('&quot;', '"').replace('&lt;', '<').replace('&gt;', '>').replace('&nbsp;', ' ')
                
                results.append(f"Title: {title}\nURL: {link}\nSnippet: {snippet}\n")
                if len(results) >= 5:
                    break
                i += 2
                continue
            i += 1
            
        if not results:
            return "No results found."
            
        return "\n".join(results)
    except Exception as e:
        return f"Error during web search: {e}"

def fetch_webpage(url):
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            html = response.read().decode('utf-8', errors='ignore')
        
        html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<p[^>]*>', '\n\n', html, flags=re.IGNORECASE)
        html = re.sub(r'<br[^>]*>', '\n', html, flags=re.IGNORECASE)
        html = re.sub(r'<h[1-6][^>]*>', '\n\n# ', html, flags=re.IGNORECASE)
        html = re.sub(r'</h[1-6]>', '\n', html, flags=re.IGNORECASE)
        
        text = re.sub(r'<[^>]+>', '', html)
        text = text.replace('&nbsp;', ' ').replace('&quot;', '"').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
        text = text.replace('&#x27;', "'").replace('&#39;', "'").replace('&ndash;', '-').replace('&mdash;', '-')
        
        lines = [line.strip() for line in text.splitlines()]
        text = "\n".join([line for line in lines if line])
        
        if len(text) > 10000:
            text = text[:10000] + "\n... (truncated)"
            
        return text
    except Exception as e:
        return f"Error fetching webpage: {e}"

MEMORY_PATH = os.path.expanduser("~/.config/ai/memory.txt")

def save_memory(content):
    try:
        os.makedirs(os.path.dirname(MEMORY_PATH), exist_ok=True)
        if len(content) > 4000:
            content = content[-4000:]
        with open(MEMORY_PATH, "w") as f:
            f.write(content)
        return "Memory updated successfully."
    except Exception as e:
        return f"Error saving memory: {e}"

def write_file(path, content):
    try:
        abs_path = os.path.abspath(os.path.expanduser(path))
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w") as f:
            f.write(content)
        return f"File successfully written to {path}"
    except Exception as e:
        return f"Error writing file: {e}"

def edit_file(path, search_content, replace_content):
    try:
        abs_path = os.path.abspath(os.path.expanduser(path))
        if not os.path.exists(abs_path):
            return f"Error: file {path} does not exist."
        with open(abs_path, "r") as f:
            content = f.read()
        if search_content not in content:
            return f"Error: search content not found in {path}. Make sure the search block matches exactly including whitespace."
        new_content = content.replace(search_content, replace_content)
        with open(abs_path, "w") as f:
            f.write(new_content)
        return f"File successfully edited at {path}"
    except Exception as e:
        return f"Error editing file: {e}"

def render_markdown(text):
    lines = text.splitlines()
    rendered = []
    in_code_block = False
    for line in lines:
        if line.startswith("```"):
            in_code_block = not in_code_block
            rendered.append("\033[90m" + "─" * 45 + "\033[0m")
            continue
        
        if in_code_block:
            rendered.append(f"  \033[36m{line}\033[0m")
            continue
            
        h_match = re.match(r'^(#{1,6})\s+(.*)', line)
        if h_match:
            level = len(h_match.group(1))
            content = h_match.group(2)
            color = "35" if level == 1 else ("34" if level == 2 else "36")
            rendered.append(f"\n\033[1;{color}m{content}\033[0m")
            continue
            
        list_match = re.match(r'^(\s*[-*+])\s+(.*)', line)
        if list_match:
            indent = list_match.group(1)[:-1]
            content = list_match.group(2)
            line = f"{indent}• {content}"
            
        num_match = re.match(r'^(\s*\d+\.)\s+(.*)', line)
        if num_match:
            prefix = num_match.group(1)
            content = num_match.group(2)
            line = f"{prefix} {content}"

        line = re.sub(r'\*\*(.*?)\*\*|__(.*?)__', r'\033[1m\1\2\033[22m', line)
        line = re.sub(r'\*(.*?)\*|_(.*?)_', r'\033[3m\1\2\033[23m', line)
        line = re.sub(r'`(.*?)`', r'\033[33m\1\033[39m', line)
        
        rendered.append(line)
        
    return "\n".join(rendered)

def main():
    if len(sys.argv) < 2:
        print("Usage: ai_mcp.py [list-tools | call-tool | render-markdown]", file=sys.stderr)
        sys.exit(1)

    action = sys.argv[1]
    mcp_servers = load_config()

    if action == "render-markdown":
        if len(sys.argv) < 3:
            sys.exit(0)
        text = sys.argv[2]
        print(render_markdown(text))
        sys.exit(0)

    if action == "list-tools":
        openai_tools = []
        
        # Native execute_command
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "execute_command",
                "description": "Run a shell command on the host system and return its stdout/stderr. Use this to inspect files, check disk space, list directories, run diagnostics, or perform tasks.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "The exact shell command to execute."
                        }
                    },
                    "required": ["command"]
                }
            }
        })

        # Native web_search
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search the web using DuckDuckGo Lite to find information, news, code examples, documentation, or facts. No API key needed.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query."
                        }
                    },
                    "required": ["query"]
                }
            }
        })

        # Native fetch_webpage
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "fetch_webpage",
                "description": "Download and read the text/markdown content of a website/URL to gather detailed information. No API key needed.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "The URL of the webpage to fetch."
                        }
                    },
                    "required": ["url"]
                }
            }
        })

        # Native save_memory
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "save_memory",
                "description": "Save key facts, user preferences, configurations, or context to persistent memory. This memory is automatically loaded as context in subsequent runs.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "The exact content to store in memory. Keep it concise."
                        }
                    },
                    "required": ["content"]
                }
            }
        })

        # Native delegate_task
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "delegate_task",
                "description": "Delegate a sub-task or investigation to a new helper AI agent. The agent runs independently with full system access and returns a summary. Use this to parallelize work or break down complex tasks.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": "The detailed instructions/task for the helper agent."
                        }
                    },
                    "required": ["task"]
                }
            }
        })

        # Native write_file
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write new content to a file (creates parent directories if needed).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "The path to the file to write to."
                        },
                        "content": {
                            "type": "string",
                            "description": "The exact content to write to the file."
                        }
                    },
                    "required": ["path", "content"]
                }
            }
        })

        # Native edit_file
        openai_tools.append({
            "type": "function",
            "function": {
                "name": "edit_file",
                "description": "Apply search-and-replace text edits to an existing file.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "The path to the file to edit."
                        },
                        "search_content": {
                            "type": "string",
                            "description": "The exact text block to search for and replace."
                        },
                        "replace_content": {
                            "type": "string",
                            "description": "The replacement text block."
                        }
                    },
                    "required": ["path", "search_content", "replace_content"]
                }
            }
        })

        for server_name, cfg in mcp_servers.items():
            tools = list_tools(server_name, cfg)
            for t in tools:
                openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("inputSchema", {
                            "type": "object",
                            "properties": {}
                        })
                    }
                })

        print(json.dumps(openai_tools))

    elif action == "call-tool":
        if len(sys.argv) < 5:
            print("Usage: ai_mcp.py call-tool <server_name> <tool_name> <arguments_json>", file=sys.stderr)
            sys.exit(1)
        
        server_name = sys.argv[2]
        tool_name = sys.argv[3]
        args_json = sys.argv[4]

        try:
            arguments = json.loads(args_json)
        except Exception as e:
            print(json.dumps({"error": f"Failed to parse arguments JSON: {e}"}))
            sys.exit(1)

        # Route custom tools
        if tool_name == "web_search" or server_name == "web_search":
            query = arguments.get("query", "")
            result = ddg_lite_search(query)
            print(result)
        elif tool_name == "fetch_webpage" or server_name == "fetch_webpage":
            url = arguments.get("url", "")
            result = fetch_webpage(url)
            print(result)
        elif tool_name == "save_memory" or server_name == "save_memory":
            content = arguments.get("content", "")
            result = save_memory(content)
            print(result)
        elif tool_name == "write_file" or server_name == "write_file":
            path = arguments.get("path", "")
            content = arguments.get("content", "")
            result = write_file(path, content)
            print(result)
        elif tool_name == "edit_file" or server_name == "edit_file":
            path = arguments.get("path", "")
            search_content = arguments.get("search_content", "")
            replace_content = arguments.get("replace_content", "")
            result = edit_file(path, search_content, replace_content)
            print(result)
        elif tool_name == "delegate_task" or server_name == "delegate_task":
            task = arguments.get("task", "")
            try:
                ai_bin = "/usr/local/bin/ai"
                if not os.path.exists(ai_bin):
                    ai_bin = "./ai"
                
                proc = subprocess.run(
                    [ai_bin, task],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=60
                )
                output = proc.stdout.strip()
                err = proc.stderr.strip()
                
                result = ""
                if output:
                    result += output
                if err:
                    result += f"\n[Agent Stderr]: {err}"
                if not result:
                    result = "Agent completed the task but returned no output."
                print(result)
            except Exception as e:
                print(f"Error delegating task: {e}")
        else:
            # Route to MCP server
            cfg = mcp_servers.get(server_name)
            if not cfg:
                # Try matching clean server name
                for k in mcp_servers.keys():
                    clean_k = "".join(c if c.isalnum() or c == "_" else "_" for k in k)
                    if clean_k == server_name:
                        cfg = mcp_servers[k]
                        break

            if not cfg:
                print(json.dumps({"error": f"MCP server '{server_name}' not found in config"}))
                sys.exit(1)

            result = call_tool(server_name, cfg, tool_name, arguments)
            print(json.dumps(result))

if __name__ == "__main__":
    main()
