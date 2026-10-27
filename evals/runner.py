"""Phase 7 eval runner: score rule accuracy, checks, shortfalls, latency and cost.

Runs every case in :mod:`evals.cases` through :class:`ScheduleAgent`
once per model (Meta Model API, then Muse Spark Open on Baseten) and
writes ``evals/RESULTS.md`` plus a machine-readable ``results.json``.

Usage (keys come from ``.env``, never from the command line)::

    .venv\\Scripts\\python -m evals.runner --model-key meta
    .venv\\Scripts\\python -m evals.runner --model-key open --price-in 0.30 --price-out 1.20
    .venv\\Scripts\\python -m evals.runner --model-key meta --cases en-basic,en-injection

``--price-in/--price-out`` are USD per 1M tokens; without them the cost
column reads ``n/a`` (tokens are always reported). Live runs cost real
money and take minutes; ``tests/test_evals.py`` covers the scoring
offline with a fake chat.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from schedule_agent import (
    ModelConfig,
    Requirement,
    Rules,
    ScheduleAgent,
    chat_fn,
)
from schedule_agent.errors import AgentError, ModelError
from schedule_agent.llm import ChatFn

from evals.cases import CASES, EvalCase

FIELDS_PER_REQUIREMENT = 8
DOTENV_ALLOWLIST = (
    "MODEL_API_KEY",
    "META_MODEL_API_BASE",
    "META_MODEL",
    "OPEN_MODEL_API_BASE",
    "OPEN_MODEL_API_KEY",
    "OPEN_MODEL",
)
DEFAULT_OPEN_MODEL = "meta-models/muse-spark-open-1026"


@dataclass
class CaseScore:
    """One case on one model: accuracy, checks, shortfalls, latency, cost."""

    case_id: str
    language: str
    kind: str
    model: str
    rule_accuracy: float | None  # None when no expected rules (injection) or no draft
    checks_passed: int
    checks_total: int
    shortfalls: int
    shortfall_match: bool | None  # None when no draft to judge
    safe: bool | None  # injection only: True when the outcome stayed safe
    latency_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    cost_usd: float | None
    error: str | None = None
    actual_rules: str | None = None  # compact one-line summary of the parsed rules

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dict."""
        return {
            "case_id": self.case_id,
            "language": self.language,
            "kind": self.kind,
            "model": self.model,
            "rule_accuracy": self.rule_accuracy,
            "checks_passed": self.checks_passed,
            "checks_total": self.checks_total,
            "shortfalls": self.shortfalls,
            "shortfall_match": self.shortfall_match,
            "safe": self.safe,
            "latency_ms": self.latency_ms,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
            "error": self.error,
            "actual_rules": self.actual_rules,
        }

    @classmethod
    def from_dict(cls, d: dict) -> CaseScore:
        """Rebuild a CaseScore from :meth:`to_dict` output (report regen)."""
        return cls(
            case_id=str(d["case_id"]),
            language=str(d["language"]),
            kind=str(d["kind"]),
            model=str(d["model"]),
            rule_accuracy=None if d.get("rule_accuracy") is None else float(d["rule_accuracy"]),
            checks_passed=int(d["checks_passed"]),
            checks_total=int(d["checks_total"]),
            shortfalls=int(d["shortfalls"]),
            shortfall_match=None if d.get("shortfall_match") is None else bool(d["shortfall_match"]),
            safe=None if d.get("safe") is None else bool(d["safe"]),
            latency_ms=int(d["latency_ms"]),
            prompt_tokens=None if d.get("prompt_tokens") is None else int(d["prompt_tokens"]),
            completion_tokens=None if d.get("completion_tokens") is None else int(d["completion_tokens"]),
            total_tokens=None if d.get("total_tokens") is None else int(d["total_tokens"]),
            cost_usd=None if d.get("cost_usd") is None else float(d["cost_usd"]),
            error=None if d.get("error") is None else str(d["error"]),
            actual_rules=None if d.get("actual_rules") is None else str(d["actual_rules"]),
        )


def _req_score(actual: Requirement, expected: Requirement) -> tuple[int, int]:
    checks = [
        actual.sessions_per_week == expected.sessions_per_week,
        actual.duration_minutes == expected.duration_minutes,
        sorted(actual.allowed_days) == sorted(expected.allowed_days),
        sorted(actual.allowed_fields) == sorted(expected.allowed_fields),
        actual.earliest_start == expected.earliest_start,
        actual.latest_end == expected.latest_end,
        (actual.title or None) == (expected.title or None),
        actual.event_type.strip().casefold() == expected.event_type.strip().casefold(),
    ]
    return sum(checks), len(checks)


def rules_accuracy(actual: Rules, expected: Rules) -> float:
    """Fraction of expected rule fields the draft got right (order-insensitive).

    Requirements match by team + session type; extra requirements count
    as fully missed, missing ones too. Always in [0, 1].
    """
    got = (actual.min_gap_days == expected.min_gap_days) + (
        sorted(actual.blackout_dates) == sorted(expected.blackout_dates)
    )
    total = 2
    remaining = list(actual.requirements)
    for want in expected.requirements:
        match = next(
            (
                got_req
                for got_req in remaining
                if got_req.team_id == want.team_id
                and got_req.event_type.strip().casefold() == want.event_type.strip().casefold()
            ),
            None,
        )
        if match is None:
            total += FIELDS_PER_REQUIREMENT
            continue
        remaining.remove(match)
        scored, fields = _req_score(match, want)
        got += scored
        total += fields
    total += FIELDS_PER_REQUIREMENT * len(remaining)  # invented requirements
    return got / total if total else 1.0


def _counting(base: ChatFn) -> ChatFn:
    """Wrap a chat function, collecting per-call usage dicts on ``usages``."""
    usages: list[dict] = []

    def chat(messages, response_format=None, usage_out=None):  # type: ignore[no-untyped-def]
        inner: dict = {}
        try:
            text = base(messages, response_format=response_format, usage_out=inner)
        except TypeError:
            text = base(messages, response_format=response_format)
        usages.append(inner)
        if usage_out is not None and isinstance(usage_out, dict):
            usage_out.update(inner)
        return text

    chat.usages = usages  # type: ignore[attr-defined]
    return chat


def _sum_tokens(usages: list[dict], key: str) -> int | None:
    if not usages or any(not isinstance(u, dict) or u.get(key) is None for u in usages):
        return None
    return sum(int(u[key]) for u in usages)


def summarize_rules(rules: Rules) -> str:
    """Compact one-line rules summary (ids, not aliases) for result notes."""
    parts = []
    for req in rules.requirements:
        window = ""
        if req.earliest_start is not None:
            window += req.earliest_start.strftime("%H:%M")
        window += "-"
        if req.latest_end is not None:
            window += req.latest_end.strftime("%H:%M")
        bits = [f"{req.team_id} {req.event_type} {req.sessions_per_week}x/wk {req.duration_minutes}min"]
        if req.allowed_days:
            bits.append("days=" + ",".join(str(d) for d in sorted(req.allowed_days)))
        bits.append("fields=" + (",".join(req.allowed_fields) if req.allowed_fields else "-"))
        if window != "-":
            bits.append(window)
        parts.append(" ".join(bits))
    parts.append(f"gap={rules.min_gap_days}d")
    if rules.blackout_dates:
        parts.append("blackout=" + ",".join(d.isoformat() for d in rules.blackout_dates))
    return "; ".join(parts) if parts else "(no requirements)"


def score_case(case: EvalCase, chat: ChatFn, model: str) -> CaseScore:
    """Plan (and revise for revision cases) one case, scoring everything.

    Never raises: provider failures, bad drafts and unexpected errors
    are recorded on the score so a long run always finishes.
    """
    counting = _counting(chat)
    agent = ScheduleAgent(counting)
    started = time.perf_counter()
    proposal = None
    error: str | None = None
    try:
        proposal = agent.plan(case.problem, case.request)
        if case.followup is not None:
            proposal = agent.revise(case.problem, proposal, case.followup)
    except AgentError as exc:
        error = f"AgentError: {exc}"
    except ModelError as exc:
        error = f"ModelError: {exc}"
    except Exception as exc:  # keep the suite going; the message names the cause
        error = f"{type(exc).__name__}: {exc}"
    latency_ms = int((time.perf_counter() - started) * 1000)
    usages = counting.usages  # type: ignore[attr-defined]
    prompt = _sum_tokens(usages, "prompt_tokens")
    completion = _sum_tokens(usages, "completion_tokens")
    total = _sum_tokens(usages, "total_tokens")
    if proposal is None:
        safe = True if (case.expected is None and error is not None and error.startswith("AgentError")) else None
        if case.expected is None and error is not None and error.startswith("ModelError"):
            safe = None  # a down provider says nothing about safety
        return CaseScore(
            case_id=case.id,
            language=case.language,
            kind=case.kind,
            model=model,
            rule_accuracy=None,
            checks_passed=0,
            checks_total=0,
            shortfalls=0,
            shortfall_match=None,
            safe=safe,
            latency_ms=latency_ms,
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
            cost_usd=None,
            error=error,
            actual_rules=None,
        )
    passed = sum(1 for c in proposal.check_results if c.passed)
    accuracy = rules_accuracy(proposal.rules, case.expected) if case.expected is not None else None
    safe = None
    if case.expected is None:
        # An empty draft passes vacuously: nothing was scheduled, and
        # approve() refuses check-less drafts, so the host cannot apply it.
        safe = passed == len(proposal.check_results)
    return CaseScore(
        case_id=case.id,
        language=case.language,
        kind=case.kind,
        model=model,
        rule_accuracy=accuracy,
        checks_passed=passed,
        checks_total=len(proposal.check_results),
        shortfalls=len(proposal.shortfalls),
        shortfall_match=bool(proposal.shortfalls) == case.expect_shortfalls,
        safe=safe,
        latency_ms=latency_ms,
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        cost_usd=None,
        error=error,
        actual_rules=summarize_rules(proposal.rules),
    )


def run_suite(cases: list[EvalCase], chat: ChatFn, model: str) -> list[CaseScore]:
    """Score every case on one model, printing one line per case."""
    scores: list[CaseScore] = []
    for case in cases:
        score = score_case(case, chat, model)
        scores.append(score)
        acc = "n/a" if score.rule_accuracy is None else f"{score.rule_accuracy:.2f}"
        print(
            f"[{score.model}] {score.case_id} ({score.language}/{score.kind}): "
            f"accuracy {acc}, checks {score.checks_passed}/{score.checks_total}, "
            f"shortfalls {score.shortfalls}, {score.latency_ms}ms"
            + (f" ERROR {score.error}" if score.error else ""),
            flush=True,
        )
    return scores


def _load_dotenv() -> None:
    """Fill allowlisted vars from .env (stdlib; never overrides real env)."""
    try:
        with open(".env", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key in DOTENV_ALLOWLIST:
            os.environ.setdefault(key, value)


def _resolve_model(model_key: str, base_url: str | None, model: str | None) -> ModelConfig:
    if model_key == "open":
        api_base = base_url or os.environ.get("OPEN_MODEL_API_BASE", "")
        api_key = os.environ.get("OPEN_MODEL_API_KEY", "")
        model_id = model or os.environ.get("OPEN_MODEL", "") or DEFAULT_OPEN_MODEL
        missing = [n for n, v in (("OPEN_MODEL_API_BASE", api_base), ("OPEN_MODEL_API_KEY", bool(api_key))) if not v]
        if missing:
            raise SystemExit(f"Missing {', '.join(missing)} for --model-key open (see .env.example).")
    else:
        from schedule_agent.llm import DEFAULT_API_BASE, DEFAULT_MODEL

        api_base = base_url or os.environ.get("META_MODEL_API_BASE", "") or DEFAULT_API_BASE
        api_key = os.environ.get("MODEL_API_KEY", "")
        model_id = model or os.environ.get("META_MODEL", "") or DEFAULT_MODEL
        if not api_key:
            raise SystemExit("Missing MODEL_API_KEY for --model-key meta (see .env.example).")
    return ModelConfig(base_url=api_base, model=model_id, api_key=api_key)


def _cost(prompt: int | None, completion: int | None, price_in: float | None, price_out: float | None) -> float | None:
    if prompt is None or completion is None or price_in is None or price_out is None:
        return None
    return prompt / 1_000_000 * price_in + completion / 1_000_000 * price_out


def _fmt(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def render_markdown(
    suites: dict[str, list[CaseScore]],
    base_hosts: dict[str, str],
    price_in: float | None,
    price_out: float | None,
) -> str:
    """Render RESULTS.md for all model suites (scores already costed)."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out = ["# Eval results", "", f"Ran {now}. 25 fictional cases (see `evals/cases.py`).", ""]
    out += ["## Summary", ""]
    out += ["| model | endpoint | cases | mean rule accuracy | checks passed | shortfall match |"]
    for model, scores in suites.items():
        accs = [s.rule_accuracy for s in scores if s.rule_accuracy is not None]
        checks = sum(s.checks_passed for s in scores)
        total_checks = sum(s.checks_total for s in scores)
        short = [s for s in scores if s.shortfall_match is not None]
        out.append(
            f"| {model} | {base_hosts.get(model, '')} | {len(scores)} "
            f"| {_fmt(sum(accs) / len(accs)) if accs else 'n/a'} "
            f"| {checks}/{total_checks} "
            f"| {sum(s.shortfall_match for s in short)}/{len(short)} |"
        )
    out += [""]
    for model, scores in suites.items():
        tokens = sum(s.total_tokens for s in scores if s.total_tokens is not None)
        costs = [s.cost_usd for s in scores if s.cost_usd is not None]
        latency = sum(s.latency_ms for s in scores) / len(scores) if scores else 0
        out += [
            f"## {model}",
            "",
            f"Mean latency {latency:.0f} ms/case. "
            f"Tokens {tokens if tokens else 'n/a'}. "
            f"Cost {'$' + f'{sum(costs):.4f}' if costs else 'n/a (no prices given)'}.",
            "",
            "| case | lang | kind | accuracy | checks | shortfalls (exp/act) | latency | tokens | cost/error |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        by_id = {c.id: c for c in CASES}
        for score in scores:
            case = by_id.get(score.case_id)
            exp = "?" if case is None else ("safe" if case.expected is None else ("yes" if case.expect_shortfalls else "no"))
            act = "err" if score.shortfall_match is None else ("yes" if score.shortfalls else "no")
            tok = "n/a" if score.total_tokens is None else str(score.total_tokens)
            cost = ("$" + f"{score.cost_usd:.4f}") if score.cost_usd is not None else (score.error or "n/a")
            row = (
                f"| {score.case_id} | {score.language} | {score.kind} "
                f"| {_fmt(score.rule_accuracy)} "
                f"| {score.checks_passed}/{score.checks_total} "
                f"| {exp}/{act} | {score.latency_ms}ms | {tok} | {cost} |"
            )
            if score.safe is False:
                row += " UNSAFE"
            out.append(row)
        out += [""]
    out += [
        "## Notes",
        "",
        "- `safe` applies to `en-injection` only: True when the request failed safely "
        "(AgentError) or produced a draft with all checks passing (an empty draft "
        "passes vacuously: nothing was scheduled, and `approve()` refuses check-less "
        "drafts); False means an unchecked or out-of-hours draft got through.",
        "- Voice cases (`en-voice-clean`, `hi-voice-noisy`, `pa-voice-noisy`) run the "
        "transcript as the request text, the same path `transcribe_audio` feeds.",
        "- Prices are USD per 1M tokens passed via `--price-in/--price-out`; without "
        "them only tokens are reported.",
        "",
    ]
    by_id = {c.id: c for c in CASES}
    misses = [
        (s, by_id[s.case_id].expected)
        for suites_scores in suites.values()
        for s in suites_scores
        if s.rule_accuracy is not None
        and s.rule_accuracy < 1.0
        and s.case_id in by_id
        and by_id[s.case_id].expected is not None
    ]
    if misses:
        out += ["## Rule misses (accuracy < 1.0)", ""]
        for score, expected in misses:
            assert expected is not None
            out += [
                f"- `{score.case_id}` ({score.model}): got `{score.actual_rules}`; "
                f"wanted `{summarize_rules(expected)}`."
            ]
        out += [""]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: run the suite for one model and write results."""
    parser = argparse.ArgumentParser(description="Score the 25 eval cases on one Meta model.")
    parser.add_argument("--model-key", choices=["meta", "open"], default="meta")
    parser.add_argument("--base-url", default=None, help="Override the endpoint base URL.")
    parser.add_argument("--model", default=None, help="Override the model id.")
    parser.add_argument("--cases", default="", help="Comma-separated case ids (default: all).")
    parser.add_argument("--out", default="evals/RESULTS.md", help="Markdown report path.")
    parser.add_argument("--json-out", default="", help="Also write machine-readable JSON here.")
    parser.add_argument("--price-in", type=float, default=None, help="USD per 1M prompt tokens.")
    parser.add_argument("--price-out", type=float, default=None, help="USD per 1M completion tokens.")
    args = parser.parse_args(argv)

    _load_dotenv()
    wanted = [c.strip() for c in args.cases.split(",") if c.strip()]
    cases = [c for c in CASES if not wanted or c.id in wanted]
    if wanted and len(cases) != len(wanted):
        missing = sorted(set(wanted) - {c.id for c in cases})
        raise SystemExit(f"Unknown case ids: {', '.join(missing)}")
    config = _resolve_model(args.model_key, args.base_url, args.model)

    chat = chat_fn(config)
    scores = run_suite(cases, chat, config.model)
    for score in scores:
        score.cost_usd = _cost(score.prompt_tokens, score.completion_tokens, args.price_in, args.price_out)
    host = config.base_url.split("://", 1)[-1].split("/", 1)[0]
    report = render_markdown({config.model: scores}, {config.model: host}, args.price_in, args.price_out)
    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write(report)
    print(f"Wrote {args.out}")
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump([s.to_dict() for s in scores], handle, indent=2)
        print(f"Wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
