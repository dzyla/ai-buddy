import importlib.util, re, sys

def _load():
    spec = importlib.util.spec_from_file_location("dr", "deep_research.py")
    dr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dr)
    return dr

def test_plan_json_structure():
    dr = _load()
    prompt = dr.build_plan_prompt("quantum computing")
    assert "quantum computing" in prompt
    assert "JSON" in prompt
    assert "questions" in prompt
    assert "search_queries" in prompt

def test_session_dir_format():
    dr = _load()
    d = dr.make_session_dir("quantum computing basics")
    assert "quantum" in str(d)
    assert "computing" in str(d)
    assert re.search(r'\d{8}_\d{6}_', str(d))

def test_fetch_prompt_contains_required_fields():
    dr = _load()
    p = dr.build_fetch_prompt("AI safety", "What are the main risks?", "AI safety risks", "/tmp/out.md")
    assert "AI safety" in p
    assert "What are the main risks?" in p
    assert "/tmp/out.md" in p
    assert "write_file" in p
    assert "task_complete" in p
    assert "training data" in p  # anti-hallucination rule

def test_extract_json_finds_embedded_json():
    dr = _load()
    text = 'Here is the plan:\n{"topic": "X", "questions": [{"id": 1}]}\nDone.'
    result = dr.extract_json(text)
    assert result["topic"] == "X"
    assert len(result["questions"]) == 1

def test_reviewer_prompt_lists_files():
    dr = _load()
    files = ["/tmp/a.md", "/tmp/b.md"]
    p = dr.build_reviewer_prompt("AI safety", files, "/tmp/review.md")
    assert "/tmp/a.md" in p
    assert "/tmp/b.md" in p
    assert "/tmp/review.md" in p
    assert "read_file" in p

def test_report_prompt_contains_citation_rules():
    dr = _load()
    p = dr.build_report_prompt("AI safety", ["/tmp/a.md"], "/tmp/review.md", "/tmp/report.md", "/tmp")
    assert "citation" in p.lower() or "[1]" in p
    assert "training data" in p
    assert "/tmp/report.md" in p
