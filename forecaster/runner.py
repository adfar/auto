"""Headless agent execution via `claude -p`.

One backend behind run_agent(); an Anthropic SDK backend can slot in later
(see docs/forecaster-design.md §6).
"""
import json
import re
import subprocess
import tempfile

MODEL = "claude-opus-4-8"
DEFAULT_TIMEOUT_S = 420


class AgentError(RuntimeError):
    """Any failure of a single agent invocation (exit code, timeout, bad JSON)."""


def run_agent(prompt: str, tools: tuple[str, ...] = ("WebSearch", "WebFetch"),
              timeout: float = DEFAULT_TIMEOUT_S) -> dict:
    """Run one headless agent call and return its parsed JSON object.

    On a JSON parse failure, retries once with an appended JSON-only nudge.
    """
    text = _invoke(prompt, tools, timeout)
    try:
        return _extract_json(text)
    except ValueError:
        text = _invoke(
            prompt + "\n\nRespond with ONLY the JSON object, no other text.",
            tools, timeout)
        try:
            return _extract_json(text)
        except ValueError as e:
            raise AgentError(str(e)) from e


def _invoke(prompt: str, tools: tuple[str, ...], timeout: float) -> str:
    cmd = ["claude", "-p", prompt, "--model", MODEL, "--output-format", "json"]
    if tools:
        cmd += ["--allowedTools", ",".join(tools)]
    try:
        # Run from a neutral cwd so the CLI doesn't load unrelated project context.
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              cwd=tempfile.gettempdir(), stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as e:
        raise AgentError(f"timed out after {timeout:.0f}s") from e
    if proc.returncode != 0:
        # Auth/usage errors land on stdout, not stderr — capture whichever has content.
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise AgentError(f"claude exited {proc.returncode}: {detail[:500]}")
    try:
        wrapper = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise AgentError(f"bad CLI wrapper output: {proc.stdout[:300]}") from e
    return wrapper.get("result", "")


def _extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError(f"no JSON in model output: {text[:300]}")
    out = json.loads(m.group(0))
    if not isinstance(out, dict):
        raise ValueError("model output JSON is not an object")
    return out
