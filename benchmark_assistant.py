#!/usr/bin/env python3
import os
import sys
import time
import json
import subprocess
import re
import shutil
import argparse
import urllib.request
from typing import Dict, List, Any, Tuple

# Configuration
CONVERSATION_ID = "4fd91220-a570-4f2a-a45a-b8b4f5bca1d5"
WORKSPACE_DIR = os.path.dirname(os.path.abspath(__file__))
ARTIFACT_DIR = f"/home/dzyla/.gemini/antigravity-cli/brain/{CONVERSATION_ID}"

def write_temp_file(name: str, content: str):
    path = os.path.join(WORKSPACE_DIR, name)
    with open(path, "w") as f:
        f.write(content)

def delete_temp_file(name: str):
    path = os.path.join(WORKSPACE_DIR, name)
    if os.path.exists(path):
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)

# ==================== TASK SET SETUPS & VALIDATORS ====================

# Task 1: Logical Reasoning & Calendar Constraints
def setup_cal():
    write_temp_file("calendar_events.txt", (
        "Existing events (July 2, 2026):\n"
        "- 09:00 - 10:30: Daily Standup\n"
        "- 11:30 - 13:00: Team Lunch\n"
        "- 14:00 - 15:30: Code Review\n"
    ))

def cleanup_cal():
    delete_temp_file("calendar_events.txt")
    delete_temp_file("free_slot.txt")

def validate_cal(stdout, stderr, exit_code, tools):
    path = os.path.join(WORKSPACE_DIR, "free_slot.txt")
    if not os.path.exists(path):
        return False
    with open(path, "r") as f:
        content = f.read().strip()
    return "10:30" in content and "11:30" in content and exit_code == 0


# Task 2: Context Extraction & Email Drafting
def setup_email():
    write_temp_file("transactions.log", (
        "[2026-07-01 08:34] ERROR: Payment failed for user alice@example.com, Amount: $120.50, Ref: TXN998822\n"
        "[2026-07-01 09:12] INFO: Refund processed for user bob@example.com, Amount: $45.00, Ref: TXN998823\n"
        "[2026-07-01 10:05] ERROR: Connection timeout for user charlie@example.com\n"
    ))

def cleanup_email():
    delete_temp_file("transactions.log")
    delete_temp_file("email_draft.txt")

def validate_email(stdout, stderr, exit_code, tools):
    path = os.path.join(WORKSPACE_DIR, "email_draft.txt")
    if not os.path.exists(path):
        return False
    with open(path, "r") as f:
        content = f.read().strip()
    return ("alice" in content.lower() or "customer" in content.lower()) and "120.50" in content and "TXN998822" in content and exit_code == 0


# Task 3: Multi-file Code Refactoring
def setup_refactoring():
    write_temp_file("math_utils.py", (
        "def multiply(a, b):\n"
        "    return a * b\n"
    ))
    write_temp_file("calculator.py", (
        "import math_utils\n"
        "\n"
        "def calculate_area(width, height):\n"
        "    return math_utils.multiply(width, height)\n"
        "\n"
        "print(\"Area:\", calculate_area(5, 10))\n"
    ))

def cleanup_refactoring():
    delete_temp_file("math_utils.py")
    delete_temp_file("calculator.py")
    delete_temp_file("__pycache__")

def validate_refactoring(stdout, stderr, exit_code, tools):
    calc_path = os.path.join(WORKSPACE_DIR, "calculator.py")
    if not os.path.exists(calc_path):
        return False
    res = subprocess.run(["python3", calc_path], capture_output=True, text=True)
    return "Area: 100" in res.stdout.strip() and exit_code == 0


# Task 4: CSV Data Processing & Aggregation
def setup_csv():
    write_temp_file("tasks.csv", (
        "task_id,task_name,duration_mins,status\n"
        "1,Code review,45,completed\n"
        "2,Research,120,completed\n"
        "3,Standup meeting,15,completed\n"
        "4,Code review,30,completed\n"
    ))

def cleanup_csv():
    delete_temp_file("tasks.csv")
    delete_temp_file("process_tasks.py")
    delete_temp_file("task_summary.json")
    delete_temp_file("__pycache__")

def validate_csv(stdout, stderr, exit_code, tools):
    path = os.path.join(WORKSPACE_DIR, "task_summary.json")
    if not os.path.exists(path):
        return False
    with open(path, "r") as f:
        data = json.load(f)
    return data.get("Code review") == 75 and data.get("Research") == 120 and data.get("Standup meeting") == 15 and exit_code == 0


# Task 5: C++ Compilation & Bug Fix
def setup_cpp():
    write_temp_file("main.cpp", (
        "#include <iostream>\n"
        "int factorial(int n) {\n"
        "    if (n == 0) return 1;\n"
        "    return n * factorial(n - 2); // Bug: should be n - 1\n"
        "}\n"
        "int main() {\n"
        "    std::cout << factorial(5) << std::endl;\n"
        "    return 0\n"  # Bug: missing semicolon
        "}\n"
    ))

def cleanup_cpp():
    delete_temp_file("main.cpp")
    delete_temp_file("main")

def validate_cpp(stdout, stderr, exit_code, tools):
    main_exe = os.path.join(WORKSPACE_DIR, "main")
    if not os.path.exists(main_exe):
        return False
    res = subprocess.run([main_exe], capture_output=True, text=True)
    return "120" in res.stdout.strip() and exit_code == 0


# Task 6: HTML/CSS/JS Stopwatch Generation
def cleanup_html():
    delete_temp_file("stopwatch.html")

def validate_html(stdout, stderr, exit_code, tools):
    path = os.path.join(WORKSPACE_DIR, "stopwatch.html")
    if not os.path.exists(path):
        return False
    with open(path, "r") as f:
        content = f.read()
    return ('id="display"' in content and 
            'id="start_btn"' in content and 
            'id="stop_btn"' in content and 
            'id="reset_btn"' in content and 
            '<script>' in content and exit_code == 0)


# Task 7: Node.js Date Sorting
def setup_nodejs():
    write_temp_file("events.json", json.dumps([
        {"name": "Event B", "date": "2026-07-02"},
        {"name": "Event A", "date": "2026-07-01"},
        {"name": "Event C", "date": "2026-07-03"}
    ]))

def cleanup_nodejs():
    delete_temp_file("events.json")
    delete_temp_file("sort_events.js")
    delete_temp_file("sorted_events.json")

def validate_nodejs(stdout, stderr, exit_code, tools):
    path = os.path.join(WORKSPACE_DIR, "sorted_events.json")
    if not os.path.exists(path):
        return False
    with open(path, "r") as f:
        data = json.load(f)
    if len(data) != 3:
        return False
    return data[0]["name"] == "Event A" and data[1]["name"] == "Event B" and data[2]["name"] == "Event C" and exit_code == 0


# Task 8: Bioinformatics Sequence Conservation Analysis
def setup_bio_seq():
    write_temp_file("aligned_seqs.fasta", (
        ">seq1\n"
        "MGDVEKGKKIFIMKCSQCHTVEKGGKHKTGPNLHGLFGRKTGQAPGYSYTAANKNKGIIW\n"
        ">seq2\n"
        "MGDVEKGKKIFIMKCSQCHTVEKGGKHKTGPNLHGLFGRKTGQAPGFSYTDANKNKGITW\n"
        ">seq3\n"
        "MGDVEKGKKIFVQKCSQCHTVEKGGKHKTGPNLHGLFGRKTGQAPGYSYTEANKNKGIIW\n"
    ))

def cleanup_bio_seq():
    delete_temp_file("aligned_seqs.fasta")
    delete_temp_file("conservation_analysis.py")
    delete_temp_file("least_conserved.json")

def validate_bio_seq(stdout, stderr, exit_code, tools):
    path = os.path.join(WORKSPACE_DIR, "least_conserved.json")
    if not os.path.exists(path):
        return False
    with open(path, "r") as f:
        data = json.load(f)
    if isinstance(data, list):
        return 47 in data and exit_code == 0
    if isinstance(data, dict):
        return (data.get("start_index") == 47 or data.get("index") == 47 or any(v == 47 for v in data.values())) and exit_code == 0
    return data == 47 and exit_code == 0


# Task 9: Publication Mock Search & GFM Table
def setup_pubmed():
    write_temp_file("pubmed_cache.json", json.dumps([
        {"title": "Gene editing of PCSK9", "authors": "Smith A, Doe B", "year": 2025, "journal": "Nature"},
        {"title": "Hemoglobin structures", "authors": "Jones C", "year": 2024, "journal": "Science"},
        {"title": "Insulin signaling review", "authors": "Smith A, Watson D", "year": 2026, "journal": "Cell"}
    ]))

def cleanup_pubmed():
    delete_temp_file("pubmed_cache.json")
    delete_temp_file("search_pub.py")
    delete_temp_file("filtered_pubs.md")

def validate_pubmed(stdout, stderr, exit_code, tools):
    path = os.path.join(WORKSPACE_DIR, "filtered_pubs.md")
    if not os.path.exists(path):
        return False
    with open(path, "r") as f:
        content = f.read()
    return "Gene editing of PCSK9" in content and "|" in content and exit_code == 0


# Task 10: Structural Biology PDB Parsing
def setup_pdb():
    write_temp_file("protein.pdb", (
        "ATOM      1  CA  ASP A   5       0.000   0.000   0.000  1.00  0.00           C\n"
        "ATOM     15  CA  LYS A  15       3.000   4.000   0.000  1.00  0.00           C\n"
    ))

def cleanup_pdb():
    delete_temp_file("protein.pdb")
    delete_temp_file("distance.py")
    delete_temp_file("dist.txt")

def validate_pdb(stdout, stderr, exit_code, tools):
    path = os.path.join(WORKSPACE_DIR, "dist.txt")
    if not os.path.exists(path):
        return False
    with open(path, "r") as f:
        val = float(f.read().strip())
    return abs(val - 5.0) < 0.01 and exit_code == 0


# Task 11: Complex RegEx Log Analysis
def setup_logs():
    write_temp_file("web_access.log", (
        "192.168.1.5 - - [01/Jul/2026:16:00:00] \"GET /index.html HTTP/1.1\" 200 1024\n"
        "192.168.1.5 - - [01/Jul/2026:16:01:00] \"GET /missing.html HTTP/1.1\" 404 512\n"
        "10.0.0.1 - - [01/Jul/2026:16:02:00] \"GET /notfound.html HTTP/1.1\" 404 256\n"
        "172.16.0.2 - - [01/Jul/2026:16:03:00] \"GET /api/data HTTP/1.1\" 500 128\n"
    ))

def cleanup_logs():
    delete_temp_file("web_access.log")
    delete_temp_file("parse_logs.py")
    delete_temp_file("failed_ips.json")

def validate_logs(stdout, stderr, exit_code, tools):
    path = os.path.join(WORKSPACE_DIR, "failed_ips.json")
    if not os.path.exists(path):
        return False
    with open(path, "r") as f:
        data = json.load(f)
    return len(data) == 2 and data[0] == "10.0.0.1" and data[1] == "192.168.1.5" and exit_code == 0


# Task 12: SQL employee aggregation
def cleanup_sqlite():
    delete_temp_file("company.db")
    delete_temp_file("db_query.py")
    delete_temp_file("salary_summary.json")

def validate_sqlite(stdout, stderr, exit_code, tools):
    path = os.path.join(WORKSPACE_DIR, "salary_summary.json")
    if not os.path.exists(path):
        return False
    with open(path, "r") as f:
        data = json.load(f)
    return "department" in data and ("average_salary" in data or "avg_salary" in data) and exit_code == 0


# Task 13: Financial Loan Amortization
def cleanup_finance():
    delete_temp_file("finance_calc.py")
    delete_temp_file("loan_amortization.csv")

def validate_finance(stdout, stderr, exit_code, tools):
    path = os.path.join(WORKSPACE_DIR, "loan_amortization.csv")
    if not os.path.exists(path):
        return False
    with open(path, "r") as f:
        lines = f.readlines()
    if len(lines) < 13:
        return False
    return "Month" in lines[0] and exit_code == 0


# Task 14: Git Conflict Resolution
def setup_conflict():
    write_temp_file("conflict.txt", (
        "<<<<<<< HEAD\n"
        "Banana\n"
        "Cherry\n"
        "=======\n"
        "Apple\n"
        "Date\n"
        ">>>>>>> main\n"
    ))

def cleanup_conflict():
    delete_temp_file("conflict.txt")
    delete_temp_file("conflict_resolved.txt")

def validate_conflict(stdout, stderr, exit_code, tools):
    path = os.path.join(WORKSPACE_DIR, "conflict_resolved.txt")
    if not os.path.exists(path):
        return False
    with open(path, "r") as f:
        content = f.read().strip().split("\n")
    expected = ["Apple", "Banana", "Cherry", "Date"]
    return [c.strip() for c in content] == expected and exit_code == 0


# Task 15: Markdown Table Transformation
def setup_table_trans():
    write_temp_file("measurements.json", json.dumps([
        {"element": "Helium", "number": 2, "melting": 1, "boiling": 4},
        {"element": "Hydrogen", "number": 1, "melting": 14, "boiling": 20}
    ]))

def cleanup_table_trans():
    delete_temp_file("measurements.json")
    delete_temp_file("table_gen.py")
    delete_temp_file("table.md")

def validate_table_trans(stdout, stderr, exit_code, tools):
    path = os.path.join(WORKSPACE_DIR, "table.md")
    if not os.path.exists(path):
        return False
    with open(path, "r") as f:
        content = f.read()
    return "Hydrogen" in content and "Helium" in content and "|" in content and exit_code == 0


# Task 16: C++ Performance Optimization (Iterative Design Loop)
def setup_cpp_opt():
    write_temp_file("optimize.cpp", (
        "#include <iostream>\n"
        "#include <chrono>\n"
        "// Slow O(N^2) trial division to find primes up to 100,000\n"
        "bool is_prime(int n) {\n"
        "    if (n <= 1) return false;\n"
        "    for (int i = 2; i < n; ++i) {\n"
        "        if (n % i == 0) return false;\n"
        "    }\n"
        "    return true;\n"
        "}\n"
        "int main() {\n"
        "    auto start = std::chrono::high_resolution_clock::now();\n"
        "    long long sum = 0;\n"
        "    for (int i = 2; i <= 100000; ++i) {\n"
        "        if (is_prime(i)) sum += i;\n"
        "    }\n"
        "    auto end = std::chrono::high_resolution_clock::now();\n"
        "    std::chrono::duration<double> diff = end - start;\n"
        "    std::cout << \"Sum: \" << sum << std::endl;\n"
        "    std::cout << \"Time: \" << diff.count() << \" s\" << std::endl;\n"
        "    return 0;\n"
        "}\n"
    ))

def cleanup_cpp_opt():
    delete_temp_file("optimize.cpp")
    delete_temp_file("optimize")

def validate_cpp_opt(stdout, stderr, exit_code, tools):
    exe_path = os.path.join(WORKSPACE_DIR, "optimize")
    if not os.path.exists(exe_path):
        return False
    start = time.time()
    res = subprocess.run([exe_path], capture_output=True, text=True)
    elapsed = time.time() - start
    has_correct_sum = "454396537" in res.stdout
    is_fast_enough = elapsed < 0.05
    print(f"C++ Optimization: sum correct={has_correct_sum}, elapsed={elapsed:.4f}s")
    return has_correct_sum and is_fast_enough and exit_code == 0


# Task 17: Portfolio Rebalancing (Stock Trading Agent Mock)
def setup_portfolio():
    write_temp_file("portfolio.json", json.dumps({
        "cash": 10000.0,
        "holdings": {
            "AAPL": {"shares": 50, "price": 200.0},
            "MSFT": {"shares": 10, "price": 400.0}
        }
    }))

def cleanup_portfolio():
    delete_temp_file("portfolio.json")
    delete_temp_file("rebalance.py")
    delete_temp_file("trades.json")

def validate_portfolio(stdout, stderr, exit_code, tools):
    path = os.path.join(WORKSPACE_DIR, "trades.json")
    if not os.path.exists(path):
        return False
    with open(path, "r") as f:
        data = json.load(f)
    aapl_trade = next((t for t in data if t.get("ticker") == "AAPL"), None)
    msft_trade = next((t for t in data if t.get("ticker") == "MSFT"), None)
    if not aapl_trade or not msft_trade:
        return False
    aapl_ok = aapl_trade.get("action") == "BUY" and aapl_trade.get("shares") == 10
    msft_ok = msft_trade.get("action") == "BUY" and msft_trade.get("shares") == 20
    return aapl_ok and msft_ok and exit_code == 0


# ==================== TEST CASES DICT ====================

TEST_CASES = [
    {
        "id": "1_calendar_reasoning",
        "name": "Logical Reasoning & Calendar Constraints",
        "setup": setup_cal,
        "prompt": "Check the calendar events in `calendar_events.txt`. Find the earliest available slot of exactly 1 hour between 09:00 and 17:00 on July 2, 2026. Note that you cannot overlap with existing events. Write the start and end time of this slot (e.g. '10:30 - 11:30') to a file named `free_slot.txt`, then call task_complete.",
        "expected_tool": "write_file",
        "validator": validate_cal,
        "cleanup": cleanup_cal
    },
    {
        "id": "2_email_extraction",
        "name": "Context Extraction & Email Drafting",
        "setup": setup_email,
        "prompt": "Analyze the error logs in `transactions.log`. Draft a polite email to the user whose payment failed. Explain that the payment of $120.50 with reference TXN998822 failed, and ask them to verify their payment method. Save the email subject on the first line and body on subsequent lines to `email_draft.txt`, then call task_complete.",
        "expected_tool": "write_file",
        "validator": validate_email,
        "cleanup": cleanup_email
    },
    {
        "id": "3_multi_file_refactor",
        "name": "Multi-file Code Refactoring (Python)",
        "setup": setup_refactoring,
        "prompt": "We want to update the multiplication function to support an optional scale factor. Edit `math_utils.py` to change `multiply(a, b)` to `multiply(a, b, scale=1.0)` returning `a * b * scale`. Edit `calculator.py` to call `math_utils.multiply(5, 10, scale=2.0)` inside `calculate_area`. Run `calculator.py` using `execute_command` to verify it prints the updated area, then call task_complete.",
        "expected_tool": "execute_command",
        "validator": validate_refactoring,
        "cleanup": cleanup_refactoring
    },
    {
        "id": "4_csv_data_processing",
        "name": "Data Processing & JSON Aggregation (CSV)",
        "setup": setup_csv,
        "prompt": "Write a python script `process_tasks.py` that reads `tasks.csv`, aggregates the total duration in minutes for each unique `task_name` (e.g. 'Code review' has 45 + 30 = 75 mins), and writes the aggregated dictionary to `task_summary.json` as JSON. Run the script using `execute_command` to generate the file, then call task_complete.",
        "expected_tool": "execute_command",
        "validator": validate_csv,
        "cleanup": cleanup_csv
    },
    {
        "id": "5_cpp_compilation_bug",
        "name": "C++ Compilation & Logic Bug Fix",
        "setup": setup_cpp,
        "prompt": "You have a C++ file `main.cpp` that contains a compilation error or logical bug. Fix `main.cpp` so it correctly compiles and computes the factorial of 5 (which should return 120). Compile it using `g++ -O2 -o main main.cpp` via `execute_command`, run `./main`, and call task_complete when the output matches 120.",
        "expected_tool": "execute_command",
        "validator": validate_cpp,
        "cleanup": cleanup_cpp
    },
    {
        "id": "6_html_css_js_widget",
        "name": "HTML/CSS/JS Stopwatch Generation",
        "setup": None,
        "prompt": "Write a single-file HTML/JS page `stopwatch.html` that implements a stopwatch. It must contain a div with id `display` showing `00:00:00`, and three buttons with ids `start_btn`, `stop_btn`, and `reset_btn` that trigger JS functions. Use clean CSS styling. Verify the file exists and has correct tags, then call task_complete.",
        "expected_tool": "write_file",
        "validator": validate_html,
        "cleanup": cleanup_html
    },
    {
        "id": "7_node_js_sorting",
        "name": "JS Node.js Date Sorting",
        "setup": setup_nodejs,
        "prompt": "Write a Node.js script `sort_events.js` that reads `events.json`, parses the dates, sorts the events chronologically (oldest to newest), and writes the sorted array to `sorted_events.json`. Run the script using `execute_command node sort_events.js` to generate the output, then call task_complete.",
        "expected_tool": "execute_command",
        "validator": validate_nodejs,
        "cleanup": cleanup_nodejs
    },
    {
        "id": "8_bio_sequence_conservation",
        "name": "Bioinformatics: Multi-Sequence Conservation sliding window",
        "setup": setup_bio_seq,
        "prompt": "You have a multi-sequence fasta file `aligned_seqs.fasta` containing 3 pre-aligned protein sequences. Write a Python script `conservation_analysis.py` that reads the fasta file, calculates the conservation score at each position (the fraction of sequences sharing the most common residue at that position), finds the 5-residue sliding window with the lowest average conservation score, and writes the 1-based start index of this window to a JSON file `least_conserved.json`. Run the script, and call task_complete.",
        "expected_tool": "execute_command",
        "validator": validate_bio_seq,
        "cleanup": cleanup_bio_seq
    },
    {
        "id": "9_pubmed_mock_search",
        "name": "NCBI PubMed Search Mocking & GFM Table",
        "setup": setup_pubmed,
        "prompt": "You have a JSON list of publication records in `pubmed_cache.json`. Write a Python script `search_pub.py` that parses this file, filters for publications authored by 'Smith' in the year 2025, formats their title and journal as a Markdown table, and writes it to `filtered_pubs.md`. Run the script, and call task_complete.",
        "expected_tool": "execute_command",
        "validator": validate_pubmed,
        "cleanup": cleanup_pubmed
    },
    {
        "id": "10_pdb_residue_dist",
        "name": "Structural Biology: PDB Residue Distance",
        "setup": setup_pdb,
        "prompt": "We have a mock PDB file `protein.pdb` containing atom records. Write a Python script `distance.py` that parses `protein.pdb`, extracts the coordinates (X, Y, Z) of the CA atom of residue 5 (Chain A) and the CA atom of residue 15 (Chain A), calculates the Euclidean distance between them in Angstroms, and writes the distance as a raw float string to `dist.txt`. Run the script, and call task_complete.",
        "expected_tool": "execute_command",
        "validator": validate_pdb,
        "cleanup": cleanup_pdb
    },
    {
        "id": "11_regex_log_analysis",
        "name": "Complex RegEx Apache Log Analysis",
        "setup": setup_logs,
        "prompt": "Analyze the log file `web_access.log`. Write a python script `parse_logs.py` that extracts all unique IP addresses that encountered a 404 status code. Write the list of unique IPs sorted alphabetically to a JSON file named `failed_ips.json`. Run the script using `execute_command`, and call task_complete.",
        "expected_tool": "execute_command",
        "validator": validate_logs,
        "cleanup": cleanup_logs
    },
    {
        "id": "12_sqlite_aggregations",
        "name": "SQL Database Execution & Window Query",
        "setup": None,
        "prompt": "Write a Python script `db_query.py` that creates a SQLite database `company.db`, creates a table `employees` with columns `id`, `name`, `department`, `salary`, populates it with 4 employees, runs a query to find the department with the highest average salary, and writes the department name and average salary to `salary_summary.json` as JSON. Run the script, and call task_complete.",
        "expected_tool": "execute_command",
        "validator": validate_sqlite,
        "cleanup": cleanup_sqlite
    },
    {
        "id": "13_finance_interest",
        "name": "Financial Calculations & Amortization Table",
        "setup": None,
        "prompt": "Write a python script `finance_calc.py` that calculates the monthly payment and total interest paid on a loan of $10,000 with an annual interest rate of 6% over 12 months (compounded monthly). Write the monthly payment, total interest, and an amortization table (columns: Month, Beginning Balance, Interest, Principal, Ending Balance) to `loan_amortization.csv`. Run the script, and call task_complete.",
        "expected_tool": "execute_command",
        "validator": validate_finance,
        "cleanup": cleanup_finance
    },
    {
        "id": "14_git_conflict_mock",
        "name": "Git Conflict Resolution Simulation",
        "setup": setup_conflict,
        "prompt": "We have a conflict file `conflict.txt` containing git conflict markers. Write a script or edit the file to resolve the conflict by keeping BOTH changes (from HEAD and from the incoming branch) sorted alphabetically, removing all git conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`). Save the resolved contents to `conflict_resolved.txt`, then call task_complete.",
        "expected_tool": "write_file",
        "validator": validate_conflict,
        "cleanup": cleanup_conflict
    },
    {
        "id": "15_markdown_table_transform",
        "name": "JSON to Markdown Table Transformation",
        "setup": setup_table_trans,
        "prompt": "You have a JSON file `measurements.json` containing scientific data. Write a python script `table_gen.py` that reads the JSON, and writes a GFM (GitHub Flavored Markdown) table containing columns: 'Element', 'Atomic Number', 'Melting Point (K)', and 'Boiling Point (K)', sorted by Atomic Number, to `table.md`. Run the script, and call task_complete.",
        "expected_tool": "execute_command",
        "validator": validate_table_trans,
        "cleanup": cleanup_table_trans
    },
    {
        "id": "16_cpp_performance_optimization",
        "name": "C++ Code Performance Optimization Loop",
        "setup": setup_cpp_opt,
        "prompt": "The C++ file `optimize.cpp` compiles and runs but is too slow. Optimize the code by replacing the slow trial-division with a fast Sieve of Eratosthenes algorithm to compute the sum of primes up to 100000. Compile using `g++ -O3 -o optimize optimize.cpp` via `execute_command`, run it, and verify it prints the correct sum and finishes in under 0.05 seconds. Call task_complete.",
        "expected_tool": "execute_command",
        "validator": validate_cpp_opt,
        "cleanup": cleanup_cpp_opt
    },
    {
        "id": "17_portfolio_rebalancing",
        "name": "Stock Trading Portfolio Rebalancing (Robinhood Mock)",
        "setup": setup_portfolio,
        "prompt": "You have a stock portfolio in `portfolio.json`. Write a Python script `rebalance.py` that calculates how many shares of AAPL and MSFT to buy or sell to rebalance the portfolio to exactly 50% AAPL and 50% MSFT (by value), using current cash. Run the script using `execute_command`, output the trades to `trades.json` as a list of dictionaries with keys `ticker`, `action` (BUY/SELL), and `shares` (rounded to nearest integer), and call task_complete.",
        "expected_tool": "execute_command",
        "validator": validate_portfolio,
        "cleanup": cleanup_portfolio
    }
]

def load_ai_env() -> Dict[str, str]:
    env = os.environ.copy()
    env_path = os.path.expanduser("~/.local/share/ai/env")
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:]
                    if "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip('"').strip("'")
                        env[k] = v
    env["INFER_AUTO_APPROVE"] = "1"
    env["INFER_TOOL_CHOICE"] = "required"
    env["INFER_TRIM_THRESHOLD"] = "45000"
    env["INFER_STUB_THRESHOLD"] = "52000"
    env["INFER_MAX_TOKENS"] = "2048"
    return env

def get_current_model_name() -> str:
    try:
        req = urllib.request.Request("http://localhost:8080/v1/models")
        with urllib.request.urlopen(req, timeout=2) as response:
            data = json.loads(response.read().decode('utf-8'))
            model_path = data.get("models", [{}])[0].get("model", "")
            return os.path.basename(model_path)
    except Exception:
        return "Unknown Local LLM"

def parse_benchmark_stats(stdout: str, stderr: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    loops_stats = []
    tools_called = []
    
    loop_pattern = re.compile(
        r'\[loop (\d+)\s*\|\s*ctx (\d+)/(\d+)\s*\(\d+%\)\s*\|\s*\+(\d+)\s*tok\s*\|\s*([\d\.]+)\s*tok/s\]'
    )
    loop_pattern_alt = re.compile(
        r'\[loop (\d+)\s*\|\s*(\d+)\s*ctx tok\s*\|\s*\+(\d+)\s*new\s*\|\s*([\d\.]+)\s*tok/s\]'
    )
    
    for line in stderr.splitlines():
        line_clean = re.sub(r'\x1b\[[0-9;]*[mGKF]', '', line).strip()
        
        if "[ai] executing command:" in line_clean:
            cmd = line_clean.split("[ai] executing command:", 1)[1].strip()
            tools_called.append(f"execute_command({cmd})")
        elif "[ai] " in line_clean and ":" in line_clean:
            parts = line_clean.split("[ai] ", 1)[1].split(":", 1)
            tool_name = parts[0].strip()
            tool_args = parts[1].strip()
            if tool_name not in ["executing command"]:
                tools_called.append(f"{tool_name}({tool_args})")
        elif "execute_command(" in line_clean:
            tools_called.append(line_clean)
                
        m = loop_pattern.search(line_clean)
        if m:
            loops_stats.append({
                "loop": int(m.group(1)),
                "ctx": int(m.group(2)),
                "tokens": int(m.group(4)),
                "tps": float(m.group(5))
            })
            continue
            
        m_alt = loop_pattern_alt.search(line_clean)
        if m_alt:
            loops_stats.append({
                "loop": int(m_alt.group(1)),
                "ctx": int(m_alt.group(2)),
                "tokens": int(m_alt.group(3)),
                "tps": float(m_alt.group(4))
            })
            
    return loops_stats, tools_called

def cleanup_mcp_processes():
    subprocess.run(["pkill", "-f", "ai_mcp.py"], capture_output=True)
    subprocess.run(["pkill", "-f", "pubmed_mcp_server.py"], capture_output=True)

def run_test_case(test: Dict[str, Any], env: Dict[str, str], timeout: int = 150) -> Dict[str, Any]:
    print(f"\n--- Running Task: {test['name']} ---")
    
    if "setup" in test and test["setup"] is not None:
        print(f"Setting up task...")
        test["setup"]()
        
    cmd = ["./ai", test["prompt"]]
    start_time = time.time()
    
    stdout, stderr = "", ""
    exit_code = -1
    elapsed = 0.0
    
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=WORKSPACE_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env
        )
        stdout, stderr = proc.communicate(timeout=timeout)
        exit_code = proc.returncode
        elapsed = time.time() - start_time
    except subprocess.TimeoutExpired:
        print(f"Task TIMEOUT reached ({timeout}s). Terminating process...")
        proc.kill()
        stdout, stderr = proc.communicate()
        elapsed = time.time() - start_time
        exit_code = -99
    except Exception as e:
        print(f"Error executing CLI: {e}")
        exit_code = -500
        
    loops, tools = parse_benchmark_stats(stdout, stderr)
    
    success = False
    if exit_code == 0:
        try:
            success = test["validator"](stdout, stderr, exit_code, tools)
        except Exception as e:
            print(f"Validator error: {e}")
            success = False
            
    if "cleanup" in test and test["cleanup"] is not None:
        print(f"Cleaning up task files...")
        test["cleanup"]()
            
    total_tokens = sum(l["tokens"] for l in loops)
    avg_tps = sum(l["tps"] for l in loops) / len(loops) if loops else 0.0
    num_loops = len(loops)
    final_ctx = loops[-1]["ctx"] if loops else 0
    
    status_str = "SUCCESS" if success else ("TIMEOUT" if exit_code == -99 else "FAILED")
    print(f"Result: {status_str} | Turns: {num_loops} | Time: {elapsed:.2f}s | Tokens: {total_tokens} | Avg Speed: {avg_tps:.1f} tok/s")
    
    return {
        "id": test["id"],
        "name": test["name"],
        "status": status_str,
        "success": success,
        "time": elapsed,
        "turns": num_loops,
        "tokens": total_tokens,
        "tps": avg_tps,
        "final_ctx": final_ctx,
        "tools": tools,
        "stdout": stdout,
        "stderr": stderr
    }

def generate_report(results: Dict[str, List[Dict[str, Any]]], run_mode: str) -> str:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    
    md = []
    md.append(f"# Google Antigravity Assistant Multi-Stage Benchmark Report")
    md.append(f"**Date:** {timestamp} | **Execution Mode:** `{run_mode}`")
    md.append("")
    md.append("This comprehensive benchmark evaluates local LLM personal assistant capabilities, including logical calendar constraint solving, structured text extraction/email drafting, multi-file code editing/refactoring, CSV data aggregation, C++ compilation & bug fix, Node.js script execution, HTML/CSS generation, bioinformatics sequence analysis, PDB parsing, SQLite database operations, financial compound interest, git conflict resolution, markdown table transformations, C++ performance iterative optimization, and stock trading portfolio rebalancing.")
    md.append("")
    
    md.append("## 📊 Executive Summary")
    md.append("")
    md.append("| Model | Success Rate | Average Turn Duration | Total Turns | Avg Generation Speed | Avg Context Growth |")
    md.append("| :--- | :---: | :---: | :---: | :---: | :---: |")
    
    for model_name, case_results in results.items():
        total_cases = len(case_results)
        successes = sum(1 for r in case_results if r["success"])
        success_rate = (successes / total_cases) * 100
        avg_time = sum(r["time"] for r in case_results) / total_cases
        total_turns = sum(r["turns"] for r in case_results)
        avg_tps = sum(r["tps"] for r in case_results if r["turns"] > 0) / sum(1 for r in case_results if r["turns"] > 0) if any(r["turns"] > 0 for r in case_results) else 0.0
        avg_ctx = sum(r["final_ctx"] for r in case_results) / total_cases
        
        md.append(f"| **{model_name}** | {success_rate:.1f}% ({successes}/{total_cases}) | {avg_time:.2f}s | {total_turns} | {avg_tps:.1f} tok/s | {avg_ctx:.0f} tok |")
    md.append("")
    
    md.append("## 🔍 Detailed Results by Model")
    md.append("")
    
    for model_name, case_results in results.items():
        md.append(f"### Model: `{model_name}`")
        md.append("")
        md.append("| Task | Status | Duration | Turns | Tokens | Speed | Tools Called |")
        md.append("| :--- | :---: | :---: | :---: | :---: | :---: | :--- |")
        
        for r in case_results:
            status_emoji = "✅ SUCCESS" if r["success"] else ("⚠️ TIMEOUT" if r["status"] == "TIMEOUT" else "❌ FAILED")
            tools_list = ", ".join([t.split("(", 1)[0] for t in r["tools"]]) if r["tools"] else "*None*"
            md.append(f"| {r['name']} | {status_emoji} | {r['time']:.2f}s | {r['turns']} | {r['tokens']} | {r['tps']:.1f} t/s | `{tools_list}` |")
        md.append("")
        
        md.append("#### Task-by-Task Analysis")
        for r in case_results:
            md.append(f"- **{r['name']}**:")
            if r["success"]:
                md.append(f"  - Completed successfully in {r['time']:.1f} seconds over {r['turns']} turns.")
                md.append(f"  - Tools utilized: `{r['tools']}`")
                md.append("  <details>")
                md.append("  <summary>🔍 View Execution Output</summary>")
                md.append("")
                md.append("  **Stdout:**")
                md.append("  ```")
                md.append(r["stdout"].strip() if r["stdout"] else "*No output*")
                md.append("  ```")
                md.append("  </details>")
            else:
                md.append(f"  - **Failure/Timeout** ({r['status']}). Process took {r['time']:.1f}s.")
                if r["tools"]:
                    md.append(f"  - Model managed to call: `{r['tools']}` before failing.")
                else:
                    md.append(f"  - No tool calls were successfully parsed from the model response.")
                
                md.append("  <details>")
                md.append("  <summary>🔍 View Execution Output & Error Logs</summary>")
                md.append("")
                md.append("  **Stdout:**")
                md.append("  ```")
                md.append(r["stdout"].strip() if r["stdout"] else "*No output*")
                md.append("  ```")
                md.append("")
                md.append("  **Stderr:**")
                md.append("  ```")
                md.append(r["stderr"].strip() if r["stderr"] else "*No error logs*")
                md.append("  ```")
                md.append("  </details>")
        md.append("")
        md.append("---")
        md.append("")
        
    md.append("## 💡 Findings and Insights")
    md.append("")
    md.append("Based on the assistant benchmark execution:")
    
    m = list(results.keys())[0]
    s = sum(1 for r in results[m] if r["success"])
    md.append(f"1. The model `{m}` achieved a success rate of {s/len(results[m])*100:.1f}% ({s}/{len(results[m])} tasks completed successfully).")
    
    return "\n".join(md)

def main():
    parser = argparse.ArgumentParser(description="Antigravity local LLM comprehensive assistant agentic benchmark runner")
    parser.add_argument("--timeout", type=int, default=150, help="Timeout in seconds per task (default: 150)")
    
    args = parser.parse_args()
    
    env = load_ai_env()
    print("=== Antigravity Assistant LLM Benchmark Initialization ===")
    print(f"Base URL: {env.get('INFER_BASE_URL')}")
    print(f"API Key: {env.get('INFER_API_KEY')}")
    print(f"Tool Choice Mode: {env.get('INFER_TOOL_CHOICE')}")
    print(f"Auto-Approve Commands: {env.get('INFER_AUTO_APPROVE')}")
    
    active_model = get_current_model_name()
    print(f"\n[Execution] Starting comprehensive assistant benchmark for active model: {active_model}")
    
    model_runs = []
    for case in TEST_CASES:
        res = run_test_case(case, env, timeout=args.timeout)
        model_runs.append(res)
        time.sleep(2)
        cleanup_mcp_processes()
        
    benchmark_results = {active_model: model_runs}
    
    # Save results to JSON
    clean_model_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in active_model)
    json_path = os.path.join(WORKSPACE_DIR, f"results_assistant_{clean_model_name}.json")
    with open(json_path, "w") as f:
        json.dump(model_runs, f, indent=2)
    print(f"[Completed] Raw assistant results saved to: {json_path}")
    
    report_md = generate_report(benchmark_results, "Comprehensive Assistant Multi-Stage Benchmark")
    
    local_report_path = os.path.join(WORKSPACE_DIR, "assistant_benchmark_report.md")
    with open(local_report_path, "w") as f:
        f.write(report_md)
    print(f"\n[Completed] Assistant report saved to workspace: {local_report_path}")
    
    if os.path.exists(ARTIFACT_DIR):
        artifact_report_path = os.path.join(ARTIFACT_DIR, "assistant_benchmark_report.md")
        with open(artifact_report_path, "w") as f:
            f.write(report_md)
        print(f"[Completed] Assistant report saved to artifacts: {artifact_report_path}")
        
    print("\n================== ASSISTANT BENCHMARK SUMMARY ==================")
    successes = sum(1 for r in model_runs if r["success"])
    print(f"Model: {active_model} | Success: {successes}/{len(model_runs)} ({successes/len(model_runs)*100:.1f}%)")
    print("============================================================")

if __name__ == "__main__":
    main()
