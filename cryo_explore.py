import hashlib
import json
import urllib.request
import urllib.parse

BASE_URL = "http://localhost:61002"
EMAIL = "dawid.zyla@cuanschutz.edu"
PASSWORD = "cryosparc_master_cli_test_2026"

def get_token():
    password_hash = hashlib.sha256(PASSWORD.encode()).hexdigest()
    data = urllib.parse.urlencode({
        "grant_type": "password",
        "username": EMAIL,
        "password": password_hash
    }).encode()
    req = urllib.request.Request(f"{BASE_URL}/token", data=data)
    resp = urllib.request.urlopen(req)
    return json.loads(resp.read().decode())["access_token"]

def api_get(path, token):
    headers = {"Authorization": f"Bearer {token}"}
    req = urllib.request.Request(f"{BASE_URL}{path}", headers=headers)
    resp = urllib.request.urlopen(req)
    return json.loads(resp.read().decode())

# Get token
token = get_token()
print("Token obtained successfully")

# List projects
projects = api_get("/projects/", token)
print(f"\n=== PROJECTS ({len(projects)}) ===")
for p in projects:
    print(f"\n  UID: {p['uid']}")
    print(f"  Title: {p['title']}")
    print(f"  Path: {p.get('path', 'N/A')}")
    print(f"  Description: {p.get('description', 'N/A')}")
    print(f"  Created: {p.get('created_at', 'N/A')}")

# List workspaces in P1
print("\n\n=== WORKSPACES (P1) ===")
try:
    workspaces = api_get("/projects/P1/workspaces", token)
    for w in workspaces:
        print(f"  {w['uid']}: {w['title']} (description: {w.get('description', 'N/A')})")
except Exception as e:
    print(f"  Error: {e}")

# List jobs in P1
print("\n\n=== JOBS (P1) ===")
try:
    jobs = api_get("/projects/P1/jobs", token)
    print(f"  Total jobs: {len(jobs)}")
    # Group by job_type
    from collections import Counter
    type_counts = Counter(j.get("job_type", "unknown") for j in jobs)
    print(f"  Job types:")
    for jtype, count in type_counts.most_common():
        print(f"    {jtype}: {count}")
except Exception as e:
    print(f"  Error: {e}")

# List sessions
print("\n\n=== SESSIONS (P1) ===")
try:
    sessions = api_get("/projects/P1/sessions", token)
    print(f"  Total sessions: {len(sessions)}")
    for s in sessions:
        print(f"\n  Session: {s.get('uid', 'N/A')}")
        print(f"  Type: {s.get('session_type', 'N/A')}")
        print(f"  Status: {s.get('status', 'N/A')}")
except Exception as e:
    print(f"  Error: {e}")

# Get openapi endpoints
print("\n\n=== API ENDPOINTS ===")
try:
    api_info = api_get("/openapi.json", token)
    paths = api_info.get("paths", {})
    print(f"  Total endpoints: {len(paths)}")
    # Print unique endpoint categories
    for path in sorted(paths.keys()):
        methods = ", ".join(paths[path].keys()).upper()
        print(f"  {methods} {path}")
except Exception as e:
    print(f"  Error: {e}")
