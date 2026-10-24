"""Running subprocesses and parsing git's progress output."""

import asyncio
import io
import sys

import pytest

from newsletter.process import CommandError, Log, Stderr, Step, parse_step, run


def test_reads_git_progress():
    assert parse_step("Receiving objects:  45% (9/20), 1.2 MiB | 3 MiB/s") == Step("receiving objects", 9, 20)


def test_reads_server_progress():
    assert parse_step("remote: Counting objects: 100% (3/3), done.") == Step("counting objects", 3, 3)


def test_ignores_other_lines():
    assert parse_step("Cloning into 'piece'...") is None
    assert parse_step("fatal: repository not found") is None


def test_stderr_reports_progress_and_logs_other_lines_split_across_chunks():
    steps: list[Step] = []
    log = Log(io.StringIO())
    stderr = Stderr(log, steps.append)
    for chunk in (b"Cloning into 'x'...\nReceiving obj", b"ects:  50% (1/2)\rReceiving objects: 100% (2/2)\n", b"done"):
        stderr.feed(chunk)
    stderr.close()
    assert steps == [Step("receiving objects", 1, 2), Step("receiving objects", 2, 2)]
    assert list(stderr.tail) == ["Cloning into 'x'...", "done"]
    assert log.stream.getvalue() == "Cloning into 'x'...\ndone\n"


SCRIPT = "import sys; sys.stderr.write('Writing objects: 50% (1/2)\\rproblem\\n'); print(sys.stdin.read().upper())"


def test_run_returns_output_and_reports_progress():
    steps: list[Step] = []
    status, out = asyncio.run(run([sys.executable, "-c", SCRIPT], on_step=steps.append, stdin="input"))
    assert (status, out) == (0, "INPUT\n")
    assert steps == [Step("writing objects", 1, 2)]


def test_run_raises_with_the_last_lines_of_output_on_failure():
    with pytest.raises(CommandError) as raised:
        asyncio.run(run([sys.executable, "-c", SCRIPT + "; sys.exit(3)"], stdin=""))
    assert raised.value.status == 3
    assert raised.value.tail == ("problem",)
