"""Subprocess sandbox for running generated code.

Component: C8 (Sandbox Test Runner), sandbox half. ``test_runner.py`` is the
other half and builds the pytest command lines that run here.

Responsibility:
    Gives every execution of generated or mutated code:
    * a fresh temporary working directory (:class:`Workspace`), deleted after
      use, so attempts cannot see each other's files;
    * a stripped environment built from a small allowlist
      (:func:`build_sandbox_env`), so no API key or user setting reaches the
      child process (spec rule 1.3);
    * a wall-clock limit enforced on the whole process tree
      (:func:`run_process`): the child starts in its own process group and on
      timeout the tree is killed with ``taskkill /F /T /PID``.

    This replaces the Docker sandbox of D2 (spec Section 3, row "Docker
    sandbox"): Docker Desktop is too heavy a requirement for the grader. The
    static checks of C7 are the first barrier and this module is the second.
    The lack of network and filesystem isolation is a recorded limitation.

Receives: file contents to place in the workspace, a command line, a timeout.
Produces: :class:`ProcessResult` (exit code, output, duration, timeout flag).
Talks to: used by ``test_runner`` (C8), ``subjects`` (C2 import check) and,
    from milestone 3, the mutation runner (C9).
Spec sections: 1.1 (Windows only), 1.3 (no key in subprocesses), 2 (Sandbox
    decision), 3 (Docker row), 5 (C8).
Feedback IDs: FB3 (timeouts are detected and classified, not guessed).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

# Environment variables copied from the parent process. Each one is needed by
# the Windows C runtime or by Python itself; nothing user specific is passed.
#   SYSTEMROOT / WINDIR: Python needs them to load system DLLs and to seed
#       its random number generator on Windows; without them imports fail.
#   COMSPEC, PATHEXT: used by subprocess-related stdlib code paths.
#   NUMBER_OF_PROCESSORS, PROCESSOR_ARCHITECTURE: read by os.cpu_count and
#       platform; harmless and avoids odd fallbacks.
ENV_ALLOWLIST: tuple[str, ...] = (
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
)

# Substrings that mark a variable as secret. Any variable containing one is
# dropped even if a later change adds it to the allowlist by mistake.
SECRET_MARKERS: tuple[str, ...] = ("KEY", "TOKEN", "SECRET", "PASSWORD")

# Windows process creation flag: start the child in a new process group so the
# group can be killed as a unit and Ctrl+C in our console does not hit it.
CREATE_NEW_PROCESS_GROUP: int = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

# Output kept from a child process. Generated tests can print without limit;
# keeping only the tail bounds memory and the size of log files.
MAX_OUTPUT_CHARS = 200_000


@dataclass
class ProcessResult:
    """Outcome of one sandboxed process.

    Attributes:
        exit_code: Process exit code, or None when the process was killed on
            timeout.
        stdout: Captured standard output (tail, at most MAX_OUTPUT_CHARS).
        stderr: Captured standard error (tail, at most MAX_OUTPUT_CHARS).
        duration_s: Wall-clock time from start to exit or kill.
        timed_out: True when the wall-clock limit was hit and the process tree
            was killed.
    """

    exit_code: int | None
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool


def build_sandbox_env(workdir: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Build the minimal environment for a sandboxed child process.

    Args:
        workdir: The workspace folder. TEMP and TMP point here so that any
            temporary file the child creates is deleted with the workspace.
        extra: Additional variables set by the caller (for example
            COVERAGE_FILE). Secret-looking names are removed from these too.

    Returns:
        A new dictionary suitable for ``subprocess.Popen(env=...)``.

    Raises:
        Nothing.

    Side effects:
        None (reads ``os.environ`` only).
    """
    env = {name: os.environ[name] for name in ENV_ALLOWLIST if name in os.environ}
    # PATH holds only the interpreter folder and System32: enough to start
    # Python and taskkill, nothing from the user's own PATH.
    system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
    env["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), str(Path(system_root) / "System32")])
    env["TEMP"] = str(workdir)
    env["TMP"] = str(workdir)
    # UTF-8 everywhere, no .pyc files in the workspace, and a fixed hash seed so
    # set and dict ordering of str keys is identical between reruns; otherwise
    # a test that depends on set order could look flaky (V6) by chance.
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONHASHSEED"] = "0"
    # Ignore the user's site-packages so only the venv's pinned packages load.
    env["PYTHONNOUSERSITE"] = "1"
    if extra:
        env.update(extra)
    for name in list(env):
        if any(marker in name.upper() for marker in SECRET_MARKERS):
            del env[name]
    return env


def kill_process_tree(pid: int) -> None:
    """Kill a process and all of its children.

    Windows has no process groups that a signal can reach, and killing only
    the direct child (``Popen.kill``) would leave grandchildren running, for
    example the pytest process started by ``coverage run``. ``taskkill /T``
    walks the process tree and ``/F`` forces termination.

    Args:
        pid: Process id of the root of the tree.

    Side effects:
        Starts ``taskkill.exe``; terminates processes.
    """
    subprocess.run(
        ["taskkill", "/F", "/T", "/PID", str(pid)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def run_process(cmd: list[str], cwd: Path, env: dict[str, str], timeout_s: float) -> ProcessResult:
    """Run a command with a wall-clock limit on its whole process tree.

    Args:
        cmd: Command line as a list (never a shell string, so nothing in it is
            interpreted by cmd.exe).
        cwd: Working directory, normally a :class:`Workspace` folder.
        env: Environment from :func:`build_sandbox_env`.
        timeout_s: Wall-clock limit in seconds.

    Returns:
        A :class:`ProcessResult`. A timeout is reported through
        ``timed_out`` rather than an exception, so callers classify it.

    Raises:
        OSError: if the executable cannot be started at all.

    Side effects:
        Starts a child process (and possibly ``taskkill``).
    """
    start = time.perf_counter()
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NEW_PROCESS_GROUP,
    )
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_process_tree(proc.pid)
        # communicate() again collects what was printed before the kill and
        # reaps the process. A second short timeout guards against a pipe held
        # open by a grandchild that taskkill could not reach.
        try:
            stdout, stderr = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
    duration = time.perf_counter() - start
    return ProcessResult(
        exit_code=None if timed_out else proc.returncode,
        stdout=(stdout or "")[-MAX_OUTPUT_CHARS:],
        stderr=(stderr or "")[-MAX_OUTPUT_CHARS:],
        duration_s=duration,
        timed_out=timed_out,
    )


class Workspace:
    """A fresh temporary folder that is deleted on exit.

    Use as a context manager::

        with Workspace() as ws:
            ws.write("mod.py", source)
            run_process([...], ws.path, build_sandbox_env(ws.path), 60)

    Attributes:
        path: Absolute path of the folder.
    """

    def __init__(self, prefix: str = "testroute_") -> None:
        """Create the folder under the system temp directory.

        Args:
            prefix: Folder name prefix, useful when inspecting leftovers.

        Side effects:
            Creates a directory.
        """
        self.path = Path(tempfile.mkdtemp(prefix=prefix))

    def write(self, name: str, content: str) -> Path:
        """Write a UTF-8 text file into the workspace and return its path."""
        target = self.path / name
        # newline="\n" keeps line numbers and byte content identical to the
        # source string, so coverage line numbers match the prompt text.
        target.write_text(content, encoding="utf-8", newline="\n")
        return target

    def cleanup(self) -> None:
        """Delete the folder.

        Windows can keep a file locked for a moment after the process that
        used it exits (antivirus scanners do this), so deletion is retried a
        few times before giving up silently; a leftover temp folder is
        harmless and the OS cleans it later.
        """
        for _ in range(5):
            shutil.rmtree(self.path, ignore_errors=True)
            if not self.path.exists():
                return
            time.sleep(0.2)

    def __enter__(self) -> "Workspace":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.cleanup()
