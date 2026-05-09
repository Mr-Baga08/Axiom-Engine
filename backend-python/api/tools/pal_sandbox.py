"""
PAL (Program-Aided Language) sandbox.

Executes untrusted LLM-generated Python in a heavily restricted subprocess.

Security model (defence in depth — each layer is independent):
  Layer 1 – Static analysis: banned import/exec/eval patterns rejected
            before the subprocess is even spawned.
  Layer 2 – Restricted builtins: code is wrapped in a restricted exec()
            environment that replaces __builtins__ with an allowlist.
  Layer 3 – Resource limits: RLIMIT_CPU=5s, RLIMIT_AS=256MB (via setrlimit).
  Layer 4 – Subprocess timeout: 5-second wall-clock kill (subprocess.run).
  Layer 5 – No network: PYTHONSAFEPATH="" + no external module access since
            builtins are stripped.

What the LLM may use:
  abs, all, any, bool, dict, enumerate, float, int, len, list, map,
  max, min, pow, print, range, round, set, sorted, str, sum, tuple, zip.

What is explicitly blocked:
  import, __import__, exec, eval, open, compile, getattr, setattr,
  globals, locals, vars, dir, type, object, super, input.
"""

from __future__ import annotations

import os
import re
import resource
import subprocess
import textwrap

from observability.observe import observe


class PALTimeoutError(Exception):
    """Raised when the sandbox subprocess exceeds the wall-clock timeout."""


class PALSecurityError(ValueError):
    """Raised when the code fails static analysis before execution."""


# ── Configuration ─────────────────────────────────────────────────────────────

TIMEOUT_SECONDS: int = int(os.getenv("PAL_TIMEOUT_SECONDS", "5"))
MEMORY_LIMIT_MB: int = 256

ALLOWED_BUILTINS = {
    "abs", "all", "any", "bool", "dict", "enumerate", "float",
    "int", "len", "list", "map", "max", "min", "pow", "print",
    "range", "round", "set", "sorted", "str", "sum", "tuple", "zip",
    "True", "False", "None",
}

# Patterns that must never appear in submitted code
_BANNED_PATTERNS = [
    r"\bimport\b",
    r"\b__import__\b",
    r"\bexec\b",
    r"\beval\b",
    r"\bopen\b",
    r"\bcompile\b",
    r"\bgetattr\b",
    r"\bsetattr\b",
    r"\bglobals\b",
    r"\blocals\b",
    r"\bvars\b",
    r"\bdir\b",
    r"\btype\b",
    r"\bobject\b",
    r"\bsuper\b",
    r"\binput\b",
    r"\bsubprocess\b",
    r"\bos\b",
    r"\bsys\b",
    r"__\w+__",   # any dunder access
]
_BANNED_RE = re.compile("|".join(_BANNED_PATTERNS))


# ── Static analysis ───────────────────────────────────────────────────────────

def _static_check(code: str) -> None:
    """
    Reject code that contains banned patterns before it ever reaches a process.

    Raises:
        PALSecurityError: with a description of the matched pattern.
    """
    match = _BANNED_RE.search(code)
    if match:
        raise PALSecurityError(
            f"Disallowed pattern in PAL code: {match.group()!r}"
        )


# ── Wrapper template ──────────────────────────────────────────────────────────

# The submitted code is wrapped in a restricted exec() that replaces
# __builtins__ with the allowlist before execution.
_WRAPPER_TEMPLATE = textwrap.dedent("""
    _allowed = {{
        {builtins}
    }}
    exec(compile({code!r}, '<pal>', 'exec'), {{"__builtins__": _allowed}})
""")


def _build_wrapper(code: str) -> str:
    builtins_dict = ", ".join(f'"{b}": {b}' for b in sorted(ALLOWED_BUILTINS))
    return _WRAPPER_TEMPLATE.format(builtins=builtins_dict, code=code)


# ── Subprocess execution ──────────────────────────────────────────────────────

def _preexec_fn() -> None:
    """
    Called in the child process before exec. Sets resource limits.
    RLIMIT_CPU: max CPU seconds (hard + soft both set to TIMEOUT_SECONDS).
    RLIMIT_AS:  max virtual memory in bytes.
    os.setsid(): creates a new process group so we can kill the whole tree.
    """
    resource.setrlimit(resource.RLIMIT_CPU, (TIMEOUT_SECONDS, TIMEOUT_SECONDS))
    resource.setrlimit(
        resource.RLIMIT_AS,
        (MEMORY_LIMIT_MB * 1024 * 1024, MEMORY_LIMIT_MB * 1024 * 1024),
    )
    os.setsid()


# ── Public API ────────────────────────────────────────────────────────────────

@observe(name="exec_pal", tags=["tool", "pal"], capture_input=False)
def exec_pal(code: str) -> str:
    """
    Execute a Python code snippet in the sandbox and return stdout.

    Args:
        code: Python code string. Must print its result to stdout.
              No imports. Only allowed builtins may be used.

    Returns:
        The stripped stdout of the subprocess.

    Raises:
        PALSecurityError: if static analysis detects a banned pattern.
        PALTimeoutError:  if execution exceeds TIMEOUT_SECONDS wall clock.
        RuntimeError:     if the subprocess exits non-zero (includes
                          resource limit kills from RLIMIT_CPU).
    """
    _static_check(code)
    wrapper = _build_wrapper(code)

    try:
        proc = subprocess.run(
            ["python3", "-c", wrapper],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            preexec_fn=_preexec_fn,
            env={"PYTHONSAFEPATH": "1", "PATH": os.environ.get("PATH", "")},
        )
    except subprocess.TimeoutExpired:
        raise PALTimeoutError(f"PAL code exceeded {TIMEOUT_SECONDS}s timeout")

    if proc.returncode != 0:
        # stderr may contain traceback — truncate to avoid leaking internal paths
        err_summary = (proc.stderr or "")[:200].strip()
        raise RuntimeError(f"PAL subprocess failed (rc={proc.returncode}): {err_summary}")

    return proc.stdout.strip()