"""Stage A: classify the question, restate the resolution criterion,
decompose into subquestions. No web access — pure question analysis."""
from . import runner, templates
from .runner import AgentError
from .spec import QuestionSpec

QUESTION_TYPES = {"event-occurrence", "quantity-threshold", "deadline-race",
                  "election-appointment", "other"}


def run(spec: QuestionSpec, timeout: float = runner.DEFAULT_TIMEOUT_S) -> dict:
    prompt = templates.render("analyst.txt", **templates.common_fields(spec))
    out = runner.run_agent(prompt, tools=(), timeout=timeout)
    return _validate(out)


def _validate(out: dict) -> dict:
    for key in ("question_type", "resolution_summary", "subquestions", "research_plan"):
        if key not in out:
            raise AgentError(f"analyst output missing '{key}'")
    subs = out["subquestions"]
    if not isinstance(subs, list) or not (1 <= len(subs) <= 8):
        raise AgentError(f"analyst returned {len(subs) if isinstance(subs, list) else 'non-list'} subquestions")
    if out["question_type"] not in QUESTION_TYPES:
        out["question_type"] = "other"
    return out
