"""Prompt template rendering shared by the stage modules.

Prompts live in prompts/*.txt as string.Template files ($placeholders) so a
prompt change is a visible diff — bump HARNESS_VERSION when they change.
"""
import string
from datetime import datetime, timezone
from pathlib import Path

from .spec import QuestionSpec

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

ASOF_NOTE = (
    "IMPORTANT: Treat the date above as the current date. Ignore any "
    "information published after it; if a source is dated later, do not use it."
)


def render(name: str, **fields) -> str:
    tpl = string.Template((PROMPTS_DIR / name).read_text())
    return tpl.substitute(**fields)


def common_fields(spec: QuestionSpec) -> dict:
    return {
        "title": spec.title,
        "detail_line": f"Detail: {spec.detail}\n" if spec.detail else "",
        "rules": spec.resolution_rules,
        "close_time": spec.close_time,
        "today": spec.as_of or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "asof_note": ASOF_NOTE if spec.as_of else "",
    }
