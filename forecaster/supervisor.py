"""Stage C: reconcile the researcher ensemble. Sees all reports
(anonymized), investigates the crux of any disagreement with targeted web
research, and produces the final raw probability."""
import json

from . import runner, templates
from .runner import AgentError
from .spec import QuestionSpec


def run(spec: QuestionSpec, analysis: dict, reports: list[dict],
        timeout: float = runner.DEFAULT_TIMEOUT_S) -> dict:
    blocks = []
    for i, r in enumerate(reports):
        blocks.append(f"--- Researcher {i + 1} ---\n{json.dumps(r, indent=2)}")
    prompt = templates.render(
        "supervisor.txt",
        **templates.common_fields(spec),
        n_reports=len(reports),
        resolution_summary=analysis["resolution_summary"],
        reports_block="\n\n".join(blocks),
    )
    out = runner.run_agent(prompt, timeout=timeout)
    return _validate(out)


def _validate(out: dict) -> dict:
    try:
        p = float(out["probability_yes"])
    except (KeyError, TypeError, ValueError) as e:
        raise AgentError(f"supervisor output has no usable probability_yes: {e}") from e
    if not (0.0 <= p <= 1.0):
        raise AgentError(f"supervisor probability out of range: {p}")
    out["probability_yes"] = p
    return out
