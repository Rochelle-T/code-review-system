from pathlib import Path

from conftest import ROOT, load_service

worker = load_service("code-analysis", "worker.py", "analysis_worker")


def test_parse_flake8_and_severity():
    out = "./pkg/a.py::3::1::F401::'os' imported but unused\n./pkg/a.py::9::5::F821::undefined name 'x'\n"
    findings = worker.parse_flake8(out)
    assert [f["code"] for f in findings] == ["F401", "F821"]
    assert findings[0]["severity"] == "warning" and findings[1]["severity"] == "error"
    assert findings[0]["file"] == "pkg/a.py"


def test_parse_bandit_handles_bad_json():
    assert worker.parse_bandit("not json") == []


def test_demo_job_finds_issues_in_samples():
    result = worker.analyse({"job_id": "t1", "repo": "demo/x", "pr_number": 1, "head_sha": "abc", "demo": True})
    tools = {f["tool"] for f in result["findings"]}
    assert result["files_analyzed"] == len(list(Path(ROOT, "services/code-analysis/samples").glob("*.py")))
    assert result["total_issues"] > 5 and {"flake8", "bandit"} <= tools
    assert result["errors"] >= 1
