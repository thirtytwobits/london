"""Asynchronous subprocesses whose output goes to a log, never to the terminal."""

from __future__ import annotations

import asyncio
import os
import re
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO

# Git reports progress on stderr as `Receiving objects:  45% (9/20)`, rewriting the line with
# carriage returns. Servers prefix their own lines with `remote: `.
_PROGRESS = re.compile(
    r"^(?:remote:\s*)?(?P<label>[A-Za-z][A-Za-z ]*?):\s+(?P<pct>\d+)% \((?P<done>\d+)/(?P<total>\d+)\)"
)
_SPLIT = re.compile(rb"[\r\n]")

TAIL_LINES = 12


@dataclass(frozen=True)
class Step:
    """One parsed progress report: what is happening and how far it has got.

    Attributes:
        label: What git is doing, in lower case, such as `receiving objects`.
        done: The units of work done.
        total: The units of work in the step.
    """

    label: str
    done: int
    total: int


OnStep = Callable[[Step], None]


def parse_step(line: str) -> Step | None:
    """Parse one line of git's progress output, or return `None` for any other line.

    Args:
        line: A line of git's standard error.
    """
    match = _PROGRESS.match(line.strip())
    if match is None:
        return None
    return Step(match["label"].lower(), int(match["done"]), int(match["total"]))


class CommandError(Exception):
    """A subprocess exited with a non-zero status.

    Args:
        argv: The command and its arguments.
        status: The exit status.
        tail: The command's last lines of output.
    """

    def __init__(self, argv: Sequence[str], status: int, tail: Sequence[str]) -> None:
        self.argv = tuple(argv)
        self.status = status
        self.tail = tuple(tail)
        super().__init__(f"{' '.join(self.argv[:3])} exited with status {status}")


@dataclass
class Log:
    """The log a subscriber's subprocess output is written to.

    Attributes:
        stream: The open log file.
    """

    stream: IO[str]

    @classmethod
    def open(cls, path: Path) -> Log:
        """Open the log at `path` for writing, creating its directory.

        Args:
            path: The log file, replaced if it exists.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        return cls(path.open("w", encoding="utf-8"))

    def write(self, line: str) -> None:
        """Append `line` to the log.

        Args:
            line: The text written, without its line ending.
        """
        self.stream.write(line + "\n")
        self.stream.flush()

    def close(self) -> None:
        """Close the log's file."""
        self.stream.close()


ENVIRONMENT = {
    # Progress labels are parsed, so they must not be translated.
    "LC_ALL": "C",
    # A prompt would hang behind the live display; fail instead.
    "GIT_TERMINAL_PROMPT": "0",
    "GH_PROMPT_DISABLED": "1",
    "GH_NO_UPDATE_NOTIFIER": "1",
    "NO_COLOR": "1",
}


class Stderr:
    """Splits a subprocess's standard error into lines as it arrives.

    Progress reports go to `on_step`; every other line goes to `log` and the last `TAIL_LINES` of
    them are kept in `tail`. Git ends progress lines with carriage returns and others with newlines.

    Args:
        log: The log the lines are written to.
        on_step: Called with each progress report.
    """

    def __init__(self, log: Log | None, on_step: OnStep | None) -> None:
        self.tail: deque[str] = deque(maxlen=TAIL_LINES)
        self._log = log
        self._on_step = on_step
        self._pending = b""

    def feed(self, chunk: bytes) -> None:
        """Take the next bytes of standard error, holding back a final incomplete line.

        Args:
            chunk: The bytes read.
        """
        *lines, self._pending = _SPLIT.split(self._pending + chunk)
        for raw in lines:
            self._line(raw)

    def close(self) -> None:
        """Take the incomplete line held back, once standard error has ended."""
        self._line(self._pending)
        self._pending = b""

    def _line(self, raw: bytes) -> None:
        line = raw.decode(errors="replace").rstrip()
        if not line:
            return
        step = parse_step(line)
        if step is not None:
            if self._on_step is not None:
                self._on_step(step)
            return
        self.tail.append(line)
        if self._log is not None:
            self._log.write(line)


async def _write(stream: asyncio.StreamWriter | None, text: str | None) -> None:
    if stream is not None and text is not None:
        stream.write(text.encode())
        await stream.drain()
        stream.close()


async def _read_into(stream: asyncio.StreamReader | None, stderr: Stderr) -> None:
    assert stream is not None
    while chunk := await stream.read(4096):
        stderr.feed(chunk)
    stderr.close()


async def _read(stream: asyncio.StreamReader | None) -> bytes:
    assert stream is not None
    return await stream.read()


async def _run(
    argv: Sequence[str], *, cwd: Path | None, log: Log | None, on_step: OnStep | None, stdin: str | None
) -> tuple[int, bytes, tuple[str, ...]]:
    """Run `argv` and return its exit status, standard output and last lines of standard error."""
    if log is not None:
        log.write("$ " + " ".join(argv))
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env={**os.environ, **ENVIRONMENT},
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stderr = Stderr(log, on_step)
    try:
        _, out, _ = await asyncio.gather(
            _write(process.stdin, stdin), _read(process.stdout), _read_into(process.stderr, stderr)
        )
        status = await process.wait()
    except asyncio.CancelledError:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    return status, out, tuple(stderr.tail)


def _log_result(log: Log | None, stdout: list[str], status: int) -> None:
    if log is not None:
        for line in stdout:
            log.write(line)
        log.write(f"[exit {status}]")


async def run(  # pylint: disable=too-many-arguments  # all but `argv` are keyword-only
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    log: Log | None = None,
    on_step: OnStep | None = None,
    stdin: str | None = None,
    check: bool = True,
) -> tuple[int, str]:
    """Run `argv` and return its exit status and standard output, decoded as UTF-8.

    Standard error is written to `log` and parsed for progress.

    Args:
        argv: The command and its arguments.
        cwd: The directory the command runs in; the current directory when absent.
        log: The log the command's output is written to.
        on_step: Called with each progress report the command prints.
        stdin: Text passed to the command on standard input.
        check: Whether a non-zero exit status raises `CommandError`.
    """
    status, out, tail = await _run(argv, cwd=cwd, log=log, on_step=on_step, stdin=stdin)
    stdout = out.decode(errors="replace")
    lines = stdout.splitlines()
    _log_result(log, lines, status)
    if check and status != 0:
        raise CommandError(argv, status, tail or lines[-TAIL_LINES:])
    return status, stdout


async def run_bytes(argv: Sequence[str], *, cwd: Path | None = None, log: Log | None = None) -> bytes:
    """Run `argv` and return its standard output unchanged, raising `CommandError` if it fails.

    Args:
        argv: The command and its arguments.
        cwd: The directory the command runs in; the current directory when absent.
        log: The log standard error and the size of standard output are written to.
    """
    status, out, tail = await _run(argv, cwd=cwd, log=log, on_step=None, stdin=None)
    _log_result(log, [f"[{len(out)} bytes of output]"], status)
    if status != 0:
        raise CommandError(argv, status, tail)
    return out
