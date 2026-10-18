from conftest import load_service

notif = load_service("notification-service", "app.py", "notification_app")


def test_comment_lists_findings():
    result = {"head_sha": "abcdef123", "files_analyzed": 2, "errors": 1, "warnings": 1, "duration_seconds": 1.2,
              "findings": [{"severity": "error", "file": "a.py", "line": 3, "tool": "flake8", "code": "F821",
                            "message": "undefined name 'x'"},
                           {"severity": "warning", "file": "a.py", "line": 9, "tool": "bandit", "code": "B105",
                            "message": "a | b"}]}
    body = notif.format_comment(result)
    assert "found **2** issue(s)" in body and "`F821`" in body and "a \\| b" in body


def test_comment_when_clean():
    body = notif.format_comment({"head_sha": "abc", "files_analyzed": 1, "findings": []})
    assert "No issues found" in body


def test_demo_jobs_are_not_posted():
    assert notif.notify({"demo": True})["status"] == "skipped"
