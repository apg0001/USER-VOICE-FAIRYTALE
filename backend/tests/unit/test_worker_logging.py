import json
import os
import subprocess
import sys


def test_worker_process_logs_redacted_json_without_traceback_locals() -> None:
    script = """
import structlog
import app.workers.celery_app

log = structlog.get_logger("worker-test")

def run(private_sentence):
    try:
        raise ValueError("boom")
    except ValueError:
        log.exception("worker_task_failed", input_text=private_sentence, job_id="j1")

run("비밀 문장입니다")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )

    assert result.returncode == 0, result.stderr
    assert "비밀 문장" not in result.stdout + result.stderr
    event = next(
        json.loads(line)
        for line in result.stdout.splitlines()
        if line.startswith("{") and "worker_task_failed" in line
    )
    assert event["input_text"] == "[REDACTED]"
    assert event["level"] == "error"
    assert "ValueError: boom" in event["exception"]
