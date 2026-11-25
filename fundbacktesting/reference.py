from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .contracts import EvaluationResult


@dataclass(frozen=True)
class ReferenceCheck:
    metric: str
    computed: float | int | None
    expected: float | int
    tolerance: float
    passed: bool


@dataclass(frozen=True)
class ReferenceReport:
    checks: tuple[ReferenceCheck, ...]
    baseline_name: str

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def as_dict(self) -> dict[str, object]:
        return {
            "baseline": self.baseline_name,
            "passed": self.passed,
            "checks": [
                {
                    "metric": check.metric,
                    "computed": check.computed,
                    "expected": check.expected,
                    "tolerance": check.tolerance,
                    "passed": check.passed,
                }
                for check in self.checks
            ],
        }


def load_baseline(path: str | Path) -> dict[str, object]:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"Baseline file does not exist: {source}")
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or "metrics" not in payload:
        raise ValueError("Baseline must be an object with a 'metrics' field.")
    return payload


def compare_to_baseline(
    evaluation: EvaluationResult, baseline: Mapping[str, object]
) -> ReferenceReport:
    """Compare computed metrics with an external baseline. The baseline is never
    used as a calculation input; it only produces a pass/fail report."""
    checks: list[ReferenceCheck] = []
    for metric, spec in baseline["metrics"].items():
        if not isinstance(spec, dict) or "value" not in spec:
            raise ValueError(f"Invalid baseline spec for metric {metric!r}.")
        expected = spec["value"]
        tolerance = float(spec.get("tolerance", 0.0))
        computed = evaluation.metrics.get(metric)
        if computed is None:
            passed = False
        else:
            passed = abs(float(computed) - float(expected)) <= tolerance
        checks.append(
            ReferenceCheck(
                metric=str(metric),
                computed=computed,
                expected=expected,
                tolerance=tolerance,
                passed=passed,
            )
        )
    return ReferenceReport(
        checks=tuple(checks),
        baseline_name=str(baseline.get("name", "unnamed")),
    )


__all__ = [
    "ReferenceCheck",
    "ReferenceReport",
    "load_baseline",
    "compare_to_baseline",
]
