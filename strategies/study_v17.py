"""研究实例的再平衡策略。

本文件是框架上的一个普通策略实现，通过 create_strategy() 加载。
它内部的准入、评分与组合构建规则属于该研究实例，不构成框架能力；
框架只看到 decide(context) 返回的目标权重。
"""
from __future__ import annotations

import math
from datetime import date
from typing import Mapping

from fundbacktesting.contracts import BacktestContext

STRATEGY_NAME = "study_v17"

WINDOW_MONTHS = 12
MIN_MDD = -0.015
CORRELATION_LIMIT = 0.90
WEIGHTS = (0.25, 0.25, 0.25, 0.25)
TARGET_SIZES = (5, 6, 7)
MIN_DAYS = 240
DEFAULT_SIZE = 6
LOCK_CODE = "RY65"
MIN_INSTITUTIONAL = 2
SWITCH_THRESHOLD_SIGMA = 0.20
SWITCH_BUDGET = 2
BUDGET_CAP = 3
INSTITUTIONAL_ORDER = ("RY65", "ESG1", "CZ", "YH", "HT")
PERMIT_START: dict[str, str] = {"YH": "2025-01-02", "HT": "2026-07-01"}
DEFAULT_RISK_FREE = 0.016


def _back_months(day: date, months: int) -> date:
    year, month = day.year, day.month - months
    while month <= 0:
        year -= 1
        month += 12
    leap = 29 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 28
    days_in_month = [31, leap, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return date(year, month, min(day.day, days_in_month[month - 1]))


def _daily_returns(values: list[float]) -> list[float]:
    return [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]


def _annualized(values: list[float], start: date, end: date) -> float:
    days = (end - start).days
    if days <= 0:
        return 0.0
    return (values[-1] / values[0]) ** (365.0 / days) - 1.0


def _volatility(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((x - mean) ** 2 for x in returns) / (len(returns) - 1)
    return math.sqrt(variance) * math.sqrt(252)


def _max_drawdown(values: list[float]) -> float:
    peak = -1e18
    drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1.0)
    return drawdown


def _average_risk_free(rates: Mapping[date, float], window: tuple[date, ...]) -> float:
    days = window[1:] if len(window) > 1 else window
    return sum(rates.get(day, DEFAULT_RISK_FREE) for day in days) / max(1, len(days))


def _correlation(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n < 20:
        return 0.0
    a, b = a[-n:], b[-n:]
    mean_a, mean_b = sum(a) / n, sum(b) / n
    var_a = math.sqrt(sum((x - mean_a) ** 2 for x in a))
    var_b = math.sqrt(sum((x - mean_b) ** 2 for x in b))
    if var_a == 0 or var_b == 0:
        return 0.0
    return sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(n)) / (var_a * var_b)


def _percentile(pairs: list[tuple[str, float]], high: bool = True) -> dict[str, float]:
    ordered = sorted(pairs, key=lambda item: item[1] if high else -item[1])
    n = len(ordered)
    result: dict[str, float] = {}
    index = 0
    while index < n:
        end = index
        while end + 1 < n and abs(ordered[end + 1][1] - ordered[index][1]) < 1e-12:
            end += 1
        rank = (index + end) / 2.0 / max(1, n - 1)
        for position in range(index, end + 1):
            result[ordered[position][0]] = rank
        index = end + 1
    return result


def _split_quarters(window: tuple[date, ...]) -> dict[str, tuple[date, ...]]:
    groups: dict[str, list[date]] = {}
    for day in window:
        key = f"{day.year}Q{(day.month - 1) // 3 + 1}"
        groups.setdefault(key, []).append(day)
    return {key: tuple(days) for key, days in groups.items() if len(days) >= 5}


def _stability(code: str, series: Mapping[str, dict[date, float]], window: tuple[date, ...]):
    quarters = _split_quarters(window)
    if len(quarters) < 2:
        return None
    hits = 0
    for days in quarters.values():
        returns = {}
        for other_code, data in series.items():
            first, last = data.get(days[0]), data.get(days[-1])
            if first and last and first > 0:
                returns[other_code] = last / first - 1.0
        if code not in returns:
            return None
        median = sorted(returns.values())[len(returns) // 2]
        if returns[code] >= median:
            hits += 1
    return hits / len(quarters)


class StudyV17Strategy:
    """带状态的再平衡策略：跨期保留上一期名单与剩余换仓额度。"""

    def __init__(self) -> None:
        self._previous: tuple[str, ...] = ()
        self._budget = SWITCH_BUDGET

    def decide(self, context: BacktestContext) -> Mapping[str, float]:
        history = context.history_before_current
        if not history:
            codes = tuple(context.active_funds[:DEFAULT_SIZE])
            return {code: 1.0 / len(codes) for code in codes} if codes else {}

        all_dates = tuple(sorted(history))
        w1 = all_dates[-1]
        window_start = _back_months(w1, WINDOW_MONTHS)
        window = tuple(day for day in all_dates if window_start <= day <= w1)

        known_codes = list(context.fund_meta or {})
        for day in all_dates:
            for code in history[day]:
                if code not in known_codes:
                    known_codes.append(code)
        series: dict[str, dict[date, float]] = {code: {} for code in known_codes}
        for day in all_dates:
            for code, value in history[day].items():
                series[code][day] = value

        rates = context.risk_free_rates or {}
        meta = context.fund_meta or {}

        def product_type(code: str) -> str:
            info = meta.get(code)
            return info.product_type if info and info.product_type else "public_fund"

        def stats_for(code: str, seg: tuple[date, ...]):
            values = [series[code].get(day) for day in seg]
            if len(values) > 20 and values[0] is None and all(v is not None for v in values[1:]):
                seg, values = seg[1:], values[1:]
            if any(v is None for v in values) or len(values) < 20:
                return None
            numeric = [float(v) for v in values]
            returns = _daily_returns(numeric)
            ann = _annualized(numeric, seg[0], seg[-1])
            vol = _volatility(returns)
            drawdown = _max_drawdown(numeric)
            sharpe = (ann - _average_risk_free(rates, seg)) / vol if vol > 0 else 0.0
            return {
                "code": code,
                "sharpe": sharpe,
                "calmar": ann / abs(drawdown) if drawdown < 0 else 0.0,
                "mdd": drawdown,
                "returns": returns,
                "institutional": product_type(code) == "insurance_asset_management",
            }

        all_codes = tuple(series)
        base_stats = {code: stats_for(code, window) for code in all_codes}

        eligible: set[str] = set()
        for code, values in base_stats.items():
            if values is None:
                continue
            if product_type(code) == "insurance_asset_management" or values["mdd"] >= MIN_MDD:
                eligible.add(code)

        decision_day = context.decision_date or context.current_date
        decision_str = decision_day.isoformat()
        candidates: list[dict[str, object]] = []
        for code in all_codes:
            if code not in eligible:
                continue
            allowed = PERMIT_START.get(code)
            if allowed and decision_str < allowed:
                continue
            item = dict(base_stats[code])
            item["stability"] = _stability(code, series, window)
            candidates.append(item)
        if not candidates:
            codes = tuple(context.active_funds[:DEFAULT_SIZE])
            return {code: 1.0 / len(codes) for code in codes} if codes else {}

        p_sharpe = _percentile([(c["code"], c["sharpe"]) for c in candidates])
        p_calmar = _percentile([(c["code"], c["calmar"]) for c in candidates])
        p_mdd = _percentile([(c["code"], c["mdd"]) for c in candidates])
        with_stability = [c for c in candidates if c["stability"] is not None]
        p_stability = (
            _percentile([(c["code"], c["stability"]) for c in with_stability])
            if len(with_stability) >= 5
            else {}
        )
        tables = (p_sharpe, p_calmar, p_mdd, p_stability)
        for candidate in candidates:
            gained = total = 0.0
            for weight, table in zip(WEIGHTS, tables):
                if candidate["code"] in table:
                    gained += weight * table[candidate["code"]]
                    total += weight
            candidate["score"] = gained / total if total else 0.0

        scores = [float(c["score"]) for c in candidates]
        mean_score = sum(scores) / len(scores)
        sigma = (
            math.sqrt(sum((x - mean_score) ** 2 for x in scores) / (len(scores) - 1))
            if len(scores) > 1
            else 0.0
        )
        ordered = sorted(candidates, key=lambda c: -float(c["score"]))
        score_map = {c["code"]: float(c["score"]) for c in candidates}
        cmap = {c["code"]: c for c in candidates}

        def has_history(code: str) -> bool:
            info = meta.get(code)
            if info is not None and info.inception_date is not None:
                return info.inception_date <= decision_day
            data = series.get(code)
            return bool(data) and min(data) <= decision_day

        def enforce_institutional(picked: list[dict[str, object]], size: int):
            def drop_public() -> bool:
                for index in range(len(picked) - 1, -1, -1):
                    if not picked[index].get("institutional"):
                        picked.pop(index)
                        return True
                return False

            have = {p["code"] for p in picked}
            need = MIN_INSTITUTIONAL - sum(1 for p in picked if p.get("institutional"))
            if need <= 0:
                return picked
            for candidate in ordered:
                if need <= 0:
                    break
                code = candidate["code"]
                if not candidate.get("institutional") or code in have:
                    continue
                if len(picked) >= size:
                    drop_public()
                picked.append(candidate)
                have.add(code)
                need -= 1
            for code in INSTITUTIONAL_ORDER:
                if need <= 0:
                    break
                if code in have or not has_history(code):
                    continue
                if len(picked) >= size:
                    drop_public()
                picked.append(
                    {
                        "code": code,
                        "score": None,
                        "institutional": True,
                        "returns": [],
                        "placeholder": True,
                    }
                )
                have.add(code)
                need -= 1
            return picked

        def base_top(size: int) -> list[dict[str, object]]:
            picked: list[dict[str, object]] = []
            have: set[str] = set()
            lock = next((c for c in candidates if c["code"] == LOCK_CODE), None)
            if lock is not None:
                picked.append(lock)
                have.add(LOCK_CODE)
            elif has_history(LOCK_CODE):
                picked.append(
                    {
                        "code": LOCK_CODE,
                        "score": None,
                        "institutional": True,
                        "returns": [],
                        "placeholder": True,
                    }
                )
                have.add(LOCK_CODE)
            for candidate in ordered:
                if len(picked) >= size:
                    break
                code = candidate["code"]
                if code in have:
                    continue
                if any(
                    _correlation(candidate["returns"], p["returns"]) > CORRELATION_LIMIT
                    for p in picked
                    if p.get("returns")
                ):
                    continue
                picked.append(candidate)
                have.add(code)
            return enforce_institutional(picked, size)[:size]

        def fill(picked: list[dict[str, object]], size: int) -> list[dict[str, object]]:
            have = {p["code"] for p in picked}
            for candidate in ordered:
                if len(picked) >= size:
                    break
                code = candidate["code"]
                if code in have:
                    continue
                if any(
                    _correlation(candidate["returns"], p["returns"]) > CORRELATION_LIMIT
                    for p in picked
                    if p.get("returns")
                ):
                    continue
                picked.append(candidate)
                have.add(code)
            return enforce_institutional(picked, size)[:size]

        def tier_performance(codes: list[str]):
            usable = [day for day in window if all(series[c].get(day) for c in codes)]
            if len(usable) < 60:
                return None
            weights = {c: 1.0 / len(codes) for c in codes}
            previous = {c: series[c][usable[0]] for c in codes}
            nav = 1.0
            curve_values = [1.0]
            for day in usable[1:]:
                day_return = sum(
                    weights[code] * (series[code][day] / previous[code] - 1.0)
                    for code in codes
                )
                nav *= 1.0 + day_return
                for code in codes:
                    weights[code] *= series[code][day] / previous[code]
                total = sum(weights.values())
                weights = {c: value / total for c, value in weights.items()}
                previous = {c: series[c][day] for c in codes}
                curve_values.append(nav)
            ann = _annualized(curve_values, usable[0], usable[-1])
            drawdown = _max_drawdown(curve_values)
            rfa = _average_risk_free(rates, usable)
            vol = _volatility(_daily_returns(curve_values))
            return {
                "ann": ann,
                "mdd": drawdown,
                "calmar": ann / abs(drawdown) if drawdown < 0 else 0.0,
                "sharpe": (ann - rfa) / vol if vol else 0.0,
                "n": len(usable),
            }

        size = DEFAULT_SIZE
        best = None
        for candidate_size in TARGET_SIZES:
            candidate_picks = base_top(candidate_size)
            performance = tier_performance([p["code"] for p in candidate_picks])
            if performance is None or performance["n"] < MIN_DAYS:
                continue
            if best is None or performance["calmar"] > best:
                best = performance["calmar"]
                size = candidate_size

        def threshold(desired: list[dict[str, object]], count: int, k: float):
            desired_codes = {p["code"] for p in desired}
            outgoing = sorted(
                [c for c in self._previous if c not in desired_codes and c in score_map],
                key=lambda c: -score_map[c],
            )
            incoming = sorted(
                [p for p in desired if p["code"] not in set(self._previous)],
                key=lambda p: -(p["score"] if p["score"] is not None else -1),
            )
            bar = k * sigma
            blocked: set[str] = set()
            restored: list[dict[str, object]] = []
            for index, candidate in enumerate(incoming):
                if index >= len(outgoing):
                    break
                old = outgoing[index]
                current_score = candidate["score"] if candidate["score"] is not None else -1
                if current_score - score_map[old] <= bar:
                    blocked.add(candidate["code"])
                    restored.append(cmap[old])
            return [p for p in desired if p["code"] not in blocked] + restored

        def pick_new(count: int, budget: int) -> list[dict[str, object]]:
            desired = base_top(count)
            selected = threshold(desired, count, SWITCH_THRESHOLD_SIGMA)
            previous_set = set(self._previous)
            incoming = [p for p in selected if p["code"] not in previous_set]
            if len(incoming) <= budget:
                return fill(selected, count)
            keep = {
                p["code"]
                for p in sorted(
                    incoming,
                    key=lambda p: -(p["score"] if p["score"] is not None else -1),
                )[:budget]
            }
            picked = [p for p in selected if p["code"] == LOCK_CODE or p["code"] in keep]
            have = {p["code"] for p in picked}
            old = sorted(
                [cmap[c] for c in previous_set if c in cmap],
                key=lambda p: -float(p["score"]),
            )
            for candidate in old:
                if len(picked) >= count:
                    break
                if candidate["code"] in have:
                    continue
                picked.append(candidate)
                have.add(candidate["code"])
            return fill(picked, count)

        if not self._previous:
            selected = base_top(size)
            final_codes = [p["code"] for p in selected][:size]
            self._previous = tuple(final_codes)
        else:
            self._budget = min(BUDGET_CAP, self._budget + SWITCH_BUDGET)
            selected = pick_new(size, self._budget)
            final_codes = [p["code"] for p in selected][:size]
            used = len(set(final_codes) - set(self._previous))
            self._budget = max(0, self._budget - used)
            self._previous = tuple(final_codes)
        if not final_codes:
            return {}
        weight = 1.0 / len(final_codes)
        return {code: weight for code in final_codes}


def create_strategy() -> StudyV17Strategy:
    return StudyV17Strategy()
