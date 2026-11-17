"""Environment check behind ``python -m testroute check-env``.

Component: support module for ``cli.py`` (spec Section 5, command
``check-env``).

Responsibility:
    Confirms that this machine can run TestRoute and prints one readable line
    per check. It is run in two modes:

    * ``--setup`` (called at the end of setup.bat): the API key may still be
      empty, because the grader pastes it after setup. A missing key is a
      warning, not a failure.
    * normal (stage 1 of run_all.bat): ``.env`` and the key are required and a
      missing key is a failure with instructions (spec Section 12).

    Ollama is always optional: it is only needed for local and ESC conditions,
    and the demo falls back to replay without it (spec Sections 3 and 12).

    When a key is present it is verified against DeepSeek's free
    ``GET /models`` endpoint (no tokens are spent), so a wrong key fails at
    setup with a clear message instead of at the first cloud call. The
    server's error text is never printed, because DeepSeek echoes the last
    characters of a rejected key in it.

Receives: the running interpreter, installed package metadata,
    ``requirements.txt``, ``config/*.yaml``, ``.env`` and the Ollama server
    named by ``OLLAMA_HOST``.
Produces: printed report lines and an exit code (0 = all required checks
    passed, 1 = at least one failed).
Talks to: ``settings`` (env and configs), ``paths`` (locations), the Ollama
    HTTP API (``GET /api/version`` and ``GET /api/tags``), the DeepSeek API
    (``GET /models`` and ``GET /user/balance``, both free).
Spec sections: 1.3 (the key is never printed), 1.8 (Python and pins),
    7 (Ollama health check), 12 (setup.bat and run_all.bat stage 1).
Feedback IDs: FB9 (automated, readable setup failures), FB12 (installed
    versions must equal the pinned versions).
"""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from testroute import paths
from testroute.settings import ConfigError, load_config, load_env_settings

# Python versions accepted by spec rule 1.8. 3.11 is the tested version.
SUPPORTED_PYTHON: tuple[tuple[int, int], ...] = ((3, 11), (3, 12))

# Every config that later components load. Parsing them here catches a broken
# edit before an experiment starts rather than halfway through one.
CONFIG_FILES: tuple[str, ...] = (
    "models.yaml",
    "pricing.yaml",
    "experiment_main.yaml",
    "experiment_pilot.yaml",
    "experiment_demo.yaml",
    "sample_run.yaml",
)

# Short timeout for the Ollama probe: an unreachable server should not make
# check-env hang, and a healthy local server answers in milliseconds.
OLLAMA_PROBE_TIMEOUT_S = 3.0

# Status labels. Plain ASCII so the output reads the same in any console code
# page (spec rule 1.6 forbids emojis).
OK, WARN, FAIL, INFO = "OK", "WARN", "FAIL", "INFO"


@dataclass
class CheckResult:
    """Outcome of one check.

    Attributes:
        status: One of OK, WARN, FAIL or INFO. Only FAIL changes the exit code.
        name: Short name of what was checked.
        detail: Human-readable explanation, including how to fix a failure.
    """

    status: str
    name: str
    detail: str


def parse_requirements(requirements_file: Path) -> dict[str, str]:
    """Read ``name==version`` pins from a requirements file.

    Args:
        requirements_file: Path to ``requirements.txt``.

    Returns:
        Mapping of normalized package name (lower case, ``_`` replaced by
        ``-``) to the pinned version string.

    Raises:
        ValueError: if a non-comment line is not an exact ``==`` pin, because
            spec rule 1.8 requires every dependency to be pinned exactly.
        OSError: if the file cannot be read.

    Side effects:
        Reads one file.
    """
    pins: dict[str, str] = {}
    for raw_line in requirements_file.read_text(encoding="utf-8").splitlines():
        # Strip trailing comments as well as whole comment lines.
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if "==" not in line:
            raise ValueError(f"requirement is not an exact pin: {line!r}")
        name, version = (part.strip() for part in line.split("==", 1))
        pins[_normalize(name)] = version
    return pins


def _normalize(name: str) -> str:
    """Normalize a distribution name so ``PyYAML`` and ``pyyaml`` match."""
    return name.lower().replace("_", "-").replace(".", "-")


def check_python() -> CheckResult:
    """Check that the interpreter is Python 3.11 or 3.12 (spec rule 1.8)."""
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info[:2] in SUPPORTED_PYTHON:
        return CheckResult(OK, "Python", f"{version} ({sys.executable})")
    return CheckResult(
        FAIL,
        "Python",
        f"{version} is not supported. Install Python 3.11 from "
        "https://www.python.org/downloads/ and run setup.bat again.",
    )


def check_packages() -> list[CheckResult]:
    """Compare installed package versions with ``requirements.txt``.

    Returns:
        One result per pinned package, plus a result for the TestRoute
        package itself. A missing package or a different version is a FAIL.

    Side effects:
        Reads ``requirements.txt`` and installed package metadata.
    """
    try:
        pins = parse_requirements(paths.REQUIREMENTS_FILE)
    except (OSError, ValueError) as exc:
        return [CheckResult(FAIL, "requirements.txt", str(exc))]

    results: list[CheckResult] = []
    for name, wanted in pins.items():
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            results.append(CheckResult(FAIL, f"package {name}", f"not installed (need {wanted}); run setup.bat"))
            continue
        if installed == wanted:
            results.append(CheckResult(OK, f"package {name}", installed))
        else:
            results.append(
                CheckResult(FAIL, f"package {name}", f"installed {installed}, pinned {wanted}; run setup.bat")
            )

    # The editable install must point at this source tree, otherwise a stale
    # copy elsewhere on the machine could be running instead of this code.
    try:
        metadata.version("testroute")
        package_dir = Path(__file__).resolve().parent
        if package_dir == (paths.PROJECT_ROOT / "src" / "testroute").resolve():
            results.append(CheckResult(OK, "package testroute", f"editable install from {package_dir}"))
        else:
            results.append(CheckResult(FAIL, "package testroute", f"imported from {package_dir}, not this project"))
    except metadata.PackageNotFoundError:
        results.append(CheckResult(FAIL, "package testroute", "not installed; run setup.bat"))
    return results


def check_configs() -> list[CheckResult]:
    """Parse every config file, resolving ``extends`` (spec Section 9.6)."""
    results: list[CheckResult] = []
    for name in CONFIG_FILES:
        try:
            load_config(name)
            results.append(CheckResult(OK, f"config {name}", "parsed"))
        except ConfigError as exc:
            results.append(CheckResult(FAIL, f"config {name}", str(exc)))
    return results


def check_output_dirs() -> CheckResult:
    """Check that every folder created by setup.bat exists."""
    missing = [d for d in paths.required_output_dirs() if not d.is_dir()]
    if not missing:
        return CheckResult(OK, "output folders", f"{len(paths.required_output_dirs())} present")
    names = ", ".join(str(d.relative_to(paths.PROJECT_ROOT)) for d in missing)
    return CheckResult(FAIL, "output folders", f"missing: {names}; run setup.bat")


def check_env_file(setup_mode: bool) -> list[CheckResult]:
    """Check ``.env`` and whether the DeepSeek key is set.

    The key value is never printed, not even partly or as a length, so the
    output of this check is safe to paste into a bug report (spec rule 1.3).

    Args:
        setup_mode: True when called from setup.bat, where an empty key is
            expected and only produces a warning.

    Returns:
        Results for the file and for the key.
    """
    env = load_env_settings()
    if not env.env_file_exists:
        return [
            CheckResult(
                FAIL,
                ".env file",
                "not found. Run setup.bat, which copies .env.example to .env, "
                "then paste your key after DEEPSEEK_API_KEY= in .env.",
            )
        ]
    results = [CheckResult(OK, ".env file", "found")]
    if env.api_key_set:
        results.append(CheckResult(OK, "DEEPSEEK_API_KEY", "set (value hidden)"))
    else:
        message = "empty. Open .env in a text editor and paste your key after DEEPSEEK_API_KEY="
        results.append(CheckResult(WARN if setup_mode else FAIL, "DEEPSEEK_API_KEY", message))
    results.append(CheckResult(INFO, "DEEPSEEK_BASE_URL", env.deepseek_base_url))
    return results


DEEPSEEK_PROBE_TIMEOUT_S = 15.0


def check_deepseek_key(setup_mode: bool, transport: "httpx.BaseTransport | None" = None) -> list[CheckResult]:
    """Verify the DeepSeek key with free endpoints, without spending tokens.

    Args:
        setup_mode: True for ``check-env --setup``. A rejected key fails in
            both modes; an unreachable API or a missing model is only a
            warning during setup (the network may be down then) and a failure
            otherwise.
        transport: Optional httpx transport (tests use a mock).

    Returns:
        Results for the key and the configured cloud model, plus an INFO line
        on whether the account has balance. Empty when no key is set (that is
        reported by :func:`check_env_file`).

    Side effects:
        Up to two HTTPS GET requests to DEEPSEEK_BASE_URL.
    """
    import httpx  # Imported here so a missing httpx is reported by check_packages, not as a crash.

    env = load_env_settings()
    if not env.api_key_set:
        return []
    soft = WARN if setup_mode else FAIL
    base = env.deepseek_base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {env.deepseek_api_key}"}
    try:
        model = str(load_config("models.yaml")["cloud"]["model"])
    except (ConfigError, KeyError, TypeError):
        model = ""
    with httpx.Client(timeout=DEEPSEEK_PROBE_TIMEOUT_S, transport=transport) as client:
        try:
            response = client.get(f"{base}/models", headers=headers)
        except httpx.HTTPError as exc:
            return [CheckResult(soft, "DeepSeek key", f"could not reach {base} ({type(exc).__name__}); key not verified")]
        if response.status_code in (401, 403):
            return [
                CheckResult(
                    FAIL,
                    "DeepSeek key",
                    f"rejected by DeepSeek (HTTP {response.status_code}). Check the key after "
                    "DEEPSEEK_API_KEY= in .env (no quotes or spaces) and run check-env again.",
                )
            ]
        if response.status_code != 200:
            return [CheckResult(soft, "DeepSeek key", f"not verified: {base}/models answered HTTP {response.status_code}")]
        try:
            listed = {m.get("id") for m in response.json().get("data", [])}
        except (ValueError, AttributeError):
            listed = set()
        results = [CheckResult(OK, "DeepSeek key", "accepted (verified with the free /models endpoint)")]
        if model and model in listed:
            results.append(CheckResult(OK, "DeepSeek model", f"{model} is available"))
        elif model:
            results.append(CheckResult(soft, "DeepSeek model", f"{model} is not in the account's model list"))
        try:
            balance = client.get(f"{base}/user/balance", headers=headers)
            available = balance.json().get("is_available") if balance.status_code == 200 else None
        except (httpx.HTTPError, ValueError, AttributeError):
            available = None
        if available is False:
            results.append(CheckResult(WARN, "DeepSeek balance", "the account has no usable balance; cloud calls will fail"))
        elif available:
            results.append(CheckResult(INFO, "DeepSeek balance", "the account has a usable balance"))
    return results


def check_ollama() -> list[CheckResult]:
    """Probe the optional Ollama server and look for the configured model.

    Never returns FAIL: Ollama is only needed for local and ESC conditions,
    and run.bat checks reachability again before a local run (spec Section 12).

    Side effects:
        Up to two HTTP GET requests to ``OLLAMA_HOST``.
    """
    import httpx  # Imported here so a missing httpx is reported by check_packages, not as a crash.

    host = load_env_settings().ollama_host.rstrip("/")
    try:
        response = httpx.get(f"{host}/api/version", timeout=OLLAMA_PROBE_TIMEOUT_S)
        response.raise_for_status()
        version = response.json().get("version", "unknown")
    except (httpx.HTTPError, ValueError):
        return [
            CheckResult(
                WARN,
                "Ollama",
                f"not reachable at {host} (optional; needed only for L-* and ESC conditions)",
            )
        ]

    results = [CheckResult(OK, "Ollama", f"version {version} at {host}")]
    try:
        model = str(load_config("models.yaml")["local"]["model"])
    except (ConfigError, KeyError, TypeError):
        # A broken models.yaml is already reported as FAIL by check_configs.
        return results
    try:
        tags = httpx.get(f"{host}/api/tags", timeout=OLLAMA_PROBE_TIMEOUT_S).json()
        names = {m.get("name") for m in tags.get("models", [])}
    except (httpx.HTTPError, ValueError, AttributeError):
        names = set()
    if model in names:
        results.append(CheckResult(OK, "Ollama model", f"{model} is pulled"))
    else:
        results.append(CheckResult(WARN, "Ollama model", f"{model} not found; run: ollama pull {model}"))
    return results


def run_check_env(setup_mode: bool = False) -> int:
    """Run every check, print the report and return an exit code.

    Args:
        setup_mode: True for ``check-env --setup`` (see module docstring).

    Returns:
        0 if no check failed, otherwise 1.

    Raises:
        Nothing; each check converts its own errors into FAIL results.

    Side effects:
        Prints to standard output, reads local files and probes Ollama.
    """
    print("TestRoute environment check" + (" (setup mode)" if setup_mode else ""))
    print(f"  Project root: {paths.PROJECT_ROOT}")
    print(f"  OS: {platform.platform()}")

    results: list[CheckResult] = [check_python()]
    results += check_packages()
    results += check_configs()
    results.append(check_output_dirs())
    results += check_env_file(setup_mode)
    results += check_deepseek_key(setup_mode)
    results += check_ollama()

    for result in results:
        print(f"  [{result.status:<4}] {result.name}: {result.detail}")

    failed = [r for r in results if r.status == FAIL]
    if failed:
        print(f"check-env: FAILED ({len(failed)} problem(s) above)")
        return 1
    print("check-env: PASSED")
    return 0
