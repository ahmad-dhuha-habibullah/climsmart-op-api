"""Fertiliser-timing risk of loss (the founder's cost-benefit method), money in Decimal.

Source: the founder's chapter on avoiding fertiliser-timing losses (Tabel 3, Subbab 1.6-1.9).
Value per application = palms x kg/palm x price/kg. Daily rain in D+1..D+4 after the application date D picks a
risk class; each class has a BAU factor and a ClimSmart-OP residual factor. Both are FOUNDER ASSUMPTIONS (editable,
config/fertiliser_timing.yaml), not measured losses. Benefit = value x (BAU factor - residual factor) = a RISK of
loss avoided, never a proven loss. Money is whole rupiah (ROUND_HALF_UP) computed with Decimal, never float.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

import yaml

CONFIG_FILE = Path(__file__).resolve().parent.parent / "config" / "fertiliser_timing.yaml"
HUNDRED = Decimal(100)


class TimingError(ValueError):
    """Bad input or not enough rain data for a complete window."""


def D(x) -> Decimal:
    """Decimal from int/str/float without binary-float noise (floats go through their shortest repr)."""
    return x if isinstance(x, Decimal) else Decimal(str(x))


def rp(x: Decimal) -> int:
    """Whole rupiah, round half up."""
    return int(D(x).quantize(Decimal(1), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class RiskClass:
    id: str
    label_en: str
    label_id: str
    rule: dict
    bau_pct: Decimal
    residual_pct: Decimal
    basis: str
    bau_range_pct: tuple[Decimal, Decimal] | None = None
    residual_range_pct: tuple[Decimal, Decimal] | None = None

    def rule_text(self, eff_mm: Decimal) -> str:
        r = self.rule
        k = r["kind"]
        if k == "sum_gt":
            return f"total rain in D+1..D+{r['days']} is more than {r['mm']:g} mm"
        if k == "no_effective_rain":
            return f"no day in D+1..D+{r['days']} has effective rain (at least {eff_mm:g} mm)"
        if k == "day_between":
            return f"a day in D+1..D+{r['days']} has {r['min_mm']:g} to {r['max_mm']:g} mm"
        return "none of the rows above applies"


@dataclass(frozen=True)
class Assumptions:
    version: str
    source_label: str
    status: str
    window_days: int
    effective_rain_mm: Decimal
    search_before_days: int
    search_after_days: int
    suggest_rank: tuple[str, ...]
    classes: tuple[RiskClass, ...]
    overridden: tuple[str, ...] = field(default=())

    def by_id(self, cid: str) -> RiskClass:
        return next(c for c in self.classes if c.id == cid)


def load_assumptions(path: Path | None = None) -> Assumptions:
    raw = yaml.safe_load((path or CONFIG_FILE).read_text(encoding="utf-8"))
    classes = []
    for c in raw["classes"]:
        classes.append(RiskClass(
            id=c["id"], label_en=c["label_en"], label_id=c["label_id"], rule=dict(c["rule"]),
            bau_pct=D(c["bau_pct"]), residual_pct=D(c["residual_pct"]), basis=c["basis"],
            bau_range_pct=tuple(D(x) for x in c["bau_range_pct"]) if c.get("bau_range_pct") else None,
            residual_range_pct=tuple(D(x) for x in c["residual_range_pct"]) if c.get("residual_range_pct") else None))
    ids = [c.id for c in classes]
    if classes[-1].rule["kind"] != "fallback" or len(set(ids)) != len(ids):
        raise ValueError("fertiliser_timing.yaml: classes must have unique ids and end with a fallback")
    if set(raw["suggest_rank"]) != set(ids):
        raise ValueError("fertiliser_timing.yaml: suggest_rank must list every class id once")
    return Assumptions(version=str(raw["version"]), source_label=raw["source_label"], status=raw["status"],
                       window_days=int(raw["window_days"]), effective_rain_mm=D(raw["effective_rain_mm"]),
                       search_before_days=int(raw["search_before_days"]), search_after_days=int(raw["search_after_days"]),
                       suggest_rank=tuple(raw["suggest_rank"]), classes=tuple(classes))


def with_overrides(a: Assumptions, overrides: dict[str, dict[str, Decimal]]) -> Assumptions:
    """overrides = {class_id: {"bau_pct": Decimal, "residual_pct": Decimal}} (any subset). Percent points, 0..100."""
    changed: list[str] = []
    classes = []
    for c in a.classes:
        o = overrides.get(c.id)
        if o:
            for k, v in o.items():
                if not (Decimal(0) <= v <= HUNDRED):
                    raise TimingError(f"{c.id}.{k} must be between 0 and 100")
            new = replace(c, bau_pct=o.get("bau_pct", c.bau_pct), residual_pct=o.get("residual_pct", c.residual_pct))
            if (new.bau_pct, new.residual_pct) != (c.bau_pct, c.residual_pct):
                changed.append(c.id)
            c = new
        classes.append(c)
    return replace(a, classes=tuple(classes), overridden=tuple(changed))


# ---- value
def block_value(palms: int, kg_per_palm, price_per_kg) -> Decimal:
    if palms < 1 or D(kg_per_palm) <= 0 or D(price_per_kg) <= 0:
        raise TimingError("palms, kg per palm and price per kg must be positive")
    return D(palms) * D(kg_per_palm) * D(price_per_kg)


# ---- classification
def classify(window: list[Decimal], a: Assumptions) -> RiskClass:
    """First matching class in config order. `window` = rain mm for D+1, D+2, ... (at least a.window_days values)."""
    if len(window) < a.window_days:
        raise TimingError(f"need {a.window_days} days of rain after the application date, got {len(window)}")
    w = [D(x) for x in window]
    if any(x < 0 for x in w):
        raise TimingError("rain cannot be negative")
    for c in a.classes:
        r = c.rule
        k = r["kind"]
        if k == "sum_gt" and sum(w[: r["days"]], Decimal(0)) > D(r["mm"]):
            return c
        if k == "no_effective_rain" and all(x < a.effective_rain_mm for x in w[: r["days"]]):
            return c
        if k == "day_between" and any(D(r["min_mm"]) <= x <= D(r["max_mm"]) for x in w[: r["days"]]):
            return c
        if k == "fallback":
            return c
    raise AssertionError("unreachable: last class is a fallback")


def assess(value: Decimal, window: list[Decimal], a: Assumptions) -> dict:
    """The founder's formula for one application: BAU risk, residual risk, avoided = BAU - residual (whole Rp)."""
    c = classify(window, a)
    bau = rp(value * c.bau_pct / HUNDRED)
    res = rp(value * c.residual_pct / HUNDRED)
    return {"cls": c, "bau_risk_rp": bau, "residual_risk_rp": res, "avoided_rp": bau - res,
            "window_total_mm": sum((D(x) for x in window[: a.window_days]), Decimal(0))}


def window_for(series: dict[date, Decimal], d: date, n: int) -> list[Decimal] | None:
    """Rain for D+1..D+n, or None when any of those days is missing."""
    out = []
    for i in range(1, n + 1):
        v = series.get(d + timedelta(days=i))
        if v is None:
            return None
        out.append(v)
    return out


def scan(series: dict[date, Decimal], first: date, last: date, a: Assumptions) -> dict[date, RiskClass]:
    """Class if the fertiliser were applied on each date in [first, last] with a complete window."""
    out: dict[date, RiskClass] = {}
    d = first
    while d <= last:
        w = window_for(series, d, a.window_days)
        if w is not None:
            out[d] = classify(w, a)
        d += timedelta(days=1)
    return out


def suggest(planned: date, scanned: dict[date, RiskClass], a: Assumptions, not_before: date | None) -> date:
    """Best date within [planned - before, planned + after] by suggest_rank; ties go to the date closest to the plan, then earlier.
    Dates before `not_before` (today in forecast mode) are not offered. If nothing beats the plan, the plan is returned."""
    rank = {cid: i for i, cid in enumerate(a.suggest_rank)}
    lo, hi = planned - timedelta(days=a.search_before_days), planned + timedelta(days=a.search_after_days)
    cands = [d for d in scanned if lo <= d <= hi and (not_before is None or d >= not_before)]
    if planned not in cands and planned in scanned:
        cands.append(planned)
    if not cands:
        return planned
    return min(cands, key=lambda d: (rank[scanned[d].id], abs((d - planned).days), d))


# ---- hindcast data
def load_daily_rain_csv(path: Path) -> dict[date, Decimal]:
    """CSV with columns date (YYYY-MM-DD) and rain_mm (the export of the founder's 'CH Harian 2024-2025' sheet)."""
    out: dict[date, Decimal] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                out[date.fromisoformat(row["date"])] = D(row["rain_mm"])
            except (InvalidOperation, ValueError, KeyError, TypeError) as e:
                raise TimingError(f"unreadable row in the daily rain file: {row}") from e
    if not out:
        raise TimingError("daily rain file is empty")
    return out
