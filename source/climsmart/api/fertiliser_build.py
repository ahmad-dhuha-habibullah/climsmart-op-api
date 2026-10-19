"""Builds the /v1/fertiliser-timing response from a daily-rain series (pure; no I/O)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from .. import fertiliser_timing as ft
from ..fertiliser_timing import D, Assumptions, TimingError

LIMITATIONS = [
    "The percentages are the founder's assumptions (risk factors from his chapter), not measured losses: this is a RISK of loss in value terms, never a proven loss.",
    "One application of one block. Application frequency, fertiliser type, topography, soil texture, ground cover and application method are not modelled.",
    "Rain is gridded (Open-Meteo) or a recorded series of unknown station; the daily forecast beyond a few days is uncertain, which is what the residual factor stands for.",
    "Rain patterns not covered by the founder's table (for example 14 to 35 mm) are 'not classified': no risk value is claimed for them.",
    "The class rules (sum over the window, a 3-day dry window, a day of 5-13 mm for 'safe') are one reading of the chapter and can be changed in config/fertiliser_timing.yaml.",
    "Scaling to a larger area repeats the block's figure per hectare; it is not an estate forecast.",
]


def _pct(x: Decimal) -> float:
    return float(x)


def _fmt(n: int, sep: str) -> str:
    return f"{n:,}".replace(",", sep)


def _kind(k: str | None) -> str:
    return k or "recorded"


def build(*, a: Assumptions, mode: str, area_ha, palms: int, kg_per_palm, price_per_kg, planned: date,
          series: dict[date, Decimal], kinds: dict[date, str], today: date | None, scale_ha,
          example_defaults: set[str], rain_source: str, rain_source_note: str) -> dict:
    n = a.window_days
    value = ft.block_value(palms, kg_per_palm, price_per_kg)
    area = D(area_ha)
    if area <= 0:
        raise TimingError("area_ha must be positive")
    w = ft.window_for(series, planned, n)
    if w is None:
        have = max(series)
        raise TimingError(f"no complete D+1..D+{n} rain window for {planned.isoformat()}; the rain data ends {have.isoformat()} "
                          f"(latest usable application date {(have - timedelta(days=n)).isoformat()})")
    scanned = ft.scan(series, min(series), max(series), a)
    sug = ft.suggest(planned, scanned, a, today)

    def day(d: date) -> dict:
        return {"date": d.isoformat(), "rain_mm": float(series[d]), "kind": _kind(kinds.get(d))}

    def assessment(d: date, extra: dict | None = None) -> tuple[dict, dict]:
        win = ft.window_for(series, d, n)
        r = ft.assess(value, win, a)
        c = r["cls"]
        out = {"application_date": d.isoformat(), "window": [day(d + timedelta(days=i)) for i in range(1, n + 1)],
               "window_rain_total_mm": float(r["window_total_mm"]), "class_id": c.id, "class_label_en": c.label_en,
               "class_label_id": c.label_id, "bau_pct": _pct(c.bau_pct), "residual_pct": _pct(c.residual_pct),
               "bau_risk_rp": r["bau_risk_rp"], "residual_risk_rp": r["residual_risk_rp"], "avoided_rp": r["avoided_rp"],
               "bau_pct_if_applied_without_advice": None}
        return out, r

    planned_out, pr = assessment(planned)
    sug_out, sr = assessment(sug)
    sug_out["bau_pct_if_applied_without_advice"] = _pct(sr["cls"].bau_pct)
    # The founder's formula: the class of the PLANNED day decides the BAU factor; the residual factor is what remains when
    # the daily forecast is used (the application moves to a safer day). So value at risk on the suggested day = residual risk.
    at_risk_planned, at_risk_sug, avoided = pr["bau_risk_rp"], pr["residual_risk_rp"], pr["avoided_rp"]
    per_ha = ft.rp(D(avoided) / area)
    moved = sug != planned
    c = pr["cls"]
    t_en = (f"If you apply on {planned:%d %b %Y}, about Rp{_fmt(at_risk_planned, ',')} of fertiliser value is at risk "
            f"({_pct(c.bau_pct):g}% assumed for '{c.label_en.lower()}'). "
            f"{'Moving to ' + format(sug, '%d %b %Y') + ' and following the daily forecast' if moved else 'Following the daily forecast'}, "
            f"the assumed residual risk is Rp{_fmt(at_risk_sug, ',')}. Difference: Rp{_fmt(avoided, ',')} "
            f"(Rp{_fmt(per_ha, ',')} per ha). A risk estimate from the founder's assumptions, not a proven loss.")
    t_id = (f"Jika dipupuk pada {planned:%d-%m-%Y}, sekitar Rp{_fmt(at_risk_planned, '.')} nilai pupuk berisiko "
            f"({_pct(c.bau_pct):g}% asumsi untuk '{c.label_id}'). "
            f"{'Dengan pindah ke ' + format(sug, '%d-%m-%Y') + ' dan mengikuti prakiraan harian' if moved else 'Dengan mengikuti prakiraan harian'}, "
            f"residual risk diasumsikan Rp{_fmt(at_risk_sug, '.')}. Selisih: Rp{_fmt(avoided, '.')} "
            f"(Rp{_fmt(per_ha, '.')}/ha). Estimasi risiko berdasarkan asumsi pendiri, bukan kerugian yang terbukti.")
    clear = sr["cls"].id in ("safe", "not_classified")
    own = sr["bau_risk_rp"]
    cons = max(0, at_risk_planned - own)
    if moved and not clear:
        t_en += (f" Caution: the suggested day is still '{sr['cls'].label_en.lower()}' ({_pct(sr['cls'].bau_pct):g}% assumed); "
                 f"using only the table's own factors the difference is Rp{_fmt(cons, ',')}.")
        t_id += (f" Catatan: hari yang disarankan masih '{sr['cls'].label_id}' ({_pct(sr['cls'].bau_pct):g}% asumsi); "
                 f"dengan faktor tabel saja selisihnya Rp{_fmt(cons, '.')}.")
    sc_ha = D(scale_ha) if scale_ha is not None else area
    first = min(planned, sug) - timedelta(days=3)
    last = max(planned, sug) + timedelta(days=n)
    days = [day(d) for d in sorted(series) if first <= d <= last]
    scan_list = [{"date": d.isoformat(), "class_id": cl.id, "bau_pct": _pct(cl.bau_pct)}
                 for d, cl in sorted(scanned.items()) if first <= d <= last]
    inputs = [
        {"name": "palms", "value": float(palms), "unit": "palms", "is_example_default": "palms" in example_defaults,
         "note": "Palms in the block"},
        {"name": "kg_per_palm", "value": float(D(kg_per_palm)), "unit": "kg per palm", "is_example_default": "kg_per_palm" in example_defaults,
         "note": "Fertiliser dose per palm"},
        {"name": "price_per_kg", "value": float(D(price_per_kg)), "unit": "Rp per kg", "is_example_default": "price_per_kg" in example_defaults,
         "note": "Fertiliser price"},
        {"name": "area_ha", "value": float(area), "unit": "ha", "is_example_default": "area_ha" in example_defaults,
         "note": "Block area (used for the per-hectare figure)"},
    ]
    assumptions = []
    for cl in a.classes:
        ov = cl.id in a.overridden
        assumptions.append({
            "id": cl.id, "label_en": cl.label_en, "label_id": cl.label_id, "rule": cl.rule_text(a.effective_rain_mm),
            "bau_pct": _pct(cl.bau_pct), "residual_pct": _pct(cl.residual_pct),
            "bau_range_pct": [float(x) for x in cl.bau_range_pct] if cl.bau_range_pct else None,
            "residual_range_pct": [float(x) for x in cl.residual_range_pct] if cl.residual_range_pct else None,
            "source": "caller override" if ov else a.source_label, "overridden": ov, "basis": cl.basis})
    return {
        "meta": {"status": a.status, "mode": mode, "rain_source": rain_source, "rain_source_note": rain_source_note,
                 "assumptions_version": a.version, "assumptions_source": a.source_label, "window_days": n,
                 "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
        "inputs": inputs,
        "block": {"area_ha": float(area), "palms": palms, "kg_per_palm": float(D(kg_per_palm)),
                  "price_per_kg_rp": ft.rp(D(price_per_kg)), "value_rp": ft.rp(value), "value_per_ha_rp": ft.rp(value / area)},
        "assumptions": assumptions, "planned": planned_out, "suggested": sug_out,
        "headline": {"value_at_risk_planned_rp": at_risk_planned, "value_at_risk_suggested_rp": at_risk_sug,
                     "avoided_rp": avoided, "avoided_per_ha_rp": per_ha, "suggested_day_class_id": sr["cls"].id,
                     "suggested_day_is_clear": clear, "suggested_day_own_risk_rp": own, "avoided_vs_suggested_day_rp": cons, "moved": moved, "text_en": t_en, "text_id": t_id},
        "scale": {"scale_ha": float(sc_ha), "avoided_per_ha_rp": per_ha, "avoided_rp": ft.rp(D(per_ha) * sc_ha),
                  "note": LIMITATIONS[1]},
        "days": days, "scan": scan_list, "limitations": LIMITATIONS,
    }
