"""Stage B: one independent research agent. K of these run in parallel;
they share a prompt and never see each other's output — ensemble diversity
comes from sampling and search paths."""
from . import runner, templates
from .runner import AgentError
from .spec import QuestionSpec


def run(spec: QuestionSpec, analysis: dict,
        timeout: float = runner.DEFAULT_TIMEOUT_S) -> dict:
    subs = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(analysis["subquestions"]))
    prompt = templates.render(
        "researcher.txt",
        **templates.common_fields(spec),
        question_type=analysis["question_type"],
        resolution_summary=analysis["resolution_summary"],
        subquestions_block=subs,
        research_plan=analysis["research_plan"],
    )
    out = runner.run_agent(prompt, timeout=timeout)
    return _validate(out)


def _validate(out: dict) -> dict:
    try:
        p = float(out["probability_yes"])
    except (KeyError, TypeError, ValueError) as e:
        raise AgentError(f"researcher output has no usable probability_yes: {e}") from e
    if not (0.0 <= p <= 1.0):
        raise AgentError(f"researcher probability out of range: {p}")
    out["probability_yes"] = p
    if not isinstance(out.get("findings"), list):
        out["findings"] = []
    return out


def sources(report: dict) -> list[str]:
    """Flat, deduped source URLs for the audit trail."""
    urls: list[str] = []
    for f in report.get("findings", []):
        for u in (f.get("sources") or []) if isinstance(f, dict) else []:
            if isinstance(u, str) and u not in urls:
                urls.append(u)
    return urls
