"""Make a working COPY of the founder's .apsimx with a daily report. The original is never touched.

Every edit is listed in the returned `edits` list and written into the result file, so a reader can
see exactly how the copy differs from the founder's model.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

DAILY_REPORT_NAME = "DailyOutput"
# (APSIM expression, column alias). Aliases are what the extractor reads back from the .db.
DAILY_VARIABLES = [
    ("[Clock].Today", "Date"),
    ("[Weather].Rain", "RainMM"),
    ("[Weather].MaxT", "MaxT"),
    ("[Weather].MinT", "MinT"),
    ("[Weather].Radn", "Radn"),
    ("[Soil].SoilWater.Eo", "EoMM"),
    ("[Soil].SoilWater.Es", "EsMM"),
    ("[OilPalm].PotentialEP", "PotentialEPMM"),
    ("[OilPalm].EP", "EPMM"),
    ("[OilPalm].UnderstoryEP", "UnderstoryEPMM"),
    ("[OilPalm].FW", "FW"),
    ("[OilPalm].UnderstoryFW", "UnderstoryFW"),
    ("[OilPalm].Fvpd", "Fvpd"),
    ("[OilPalm].LAI", "LAI"),
    ("[OilPalm].Age", "PalmAgeYears"),
    ("sum([Soil].Water.MM)", "SoilWaterMM"),
    ("sum([Soil].Physical.LL15mm)", "LL15MM"),
    ("sum([Soil].Physical.DULmm)", "DULMM"),
    ("sum([Soil].Physical.SATmm)", "SATMM"),
    ("[Soil].SoilWater.Runoff", "RunoffMM"),
    ("[Soil].SoilWater.Drainage", "DrainageMM"),
    ("[Irrigation].IrrigationApplied", "IrrigationMM"),
    ("[Fertiliser].NitrogenApplied", "FertNKgHa"),
    ("[Soil].SoilWater.LeachNO3", "LeachNO3KgHa"),
    ("[Calculations].Script.AnnualYield", "YTDYieldFFBtHa"),
]


def _find(node: dict, pred):
    if pred(node):
        return node
    for c in node.get("Children", []):
        r = _find(c, pred)
        if r is not None:
            return r
    return None


def make_daily_copy(src: Path, dst: Path, weather_file_name: str, clock_start: str | None = None,
                    clock_end: str | None = None, sowing_year: int | None = None,
                    mutate=None) -> list[str]:
    """Write `dst` from `src` with: weather path set, optional Clock dates and planting year, daily report added.

    sowing_year: plant on 1 Jan of that year and start the clock the same day. The founder's file plants in
    2001; run to 2026 the palm is >25 years old and the model breaks (negative LAI -> NaN), so the planting
    year is moved later so the palm is mid-life in 2026. This is an ASSUMPTION, not the estate's real age.
    """
    model = json.loads(Path(src).read_text(encoding="utf-8"))
    out = copy.deepcopy(model)
    edits: list[str] = []

    weather = _find(out, lambda n: n.get("$type", "").startswith("Models.Climate.Weather"))
    if weather is None:
        raise ValueError("no Weather model found in the .apsimx")
    edits.append(f"Weather.FileName: '{weather.get('FileName')}' -> '{weather_file_name}'")
    weather["FileName"] = weather_file_name

    clock = _find(out, lambda n: n.get("$type", "").startswith("Models.Clock"))
    if clock is None:
        raise ValueError("no Clock model found in the .apsimx")
    for key, val in (("Start", clock_start), ("End", clock_end)):
        if val:
            edits.append(f"Clock.{key}: '{clock.get(key)}' -> '{val}'")
            clock[key] = val

    if sowing_year:
        mgr = _find(out, lambda n: n.get("Name") == "Palm Management")
        if mgr is None:
            raise ValueError("no 'Palm Management' manager found")
        for p in mgr["Parameters"]:
            if p["Key"] == "sowing_date":
                edits.append(f"Palm Management.sowing_date: '{p['Value']}' -> '01/01/{sowing_year} 00:00:00'")
                p["Value"] = f"01/01/{sowing_year} 00:00:00"
        edits.append(f"Clock.Start: '{clock.get('Start')}' -> '{sowing_year}-01-01T00:00:00' (clock starts on planting day)")
        clock["Start"] = f"{sowing_year}-01-01T00:00:00"

    if mutate is not None:
        edits.extend(mutate(out))

    zone = _find(out, lambda n: n.get("$type", "").startswith("Models.Core.Zone"))
    if zone is None:
        raise ValueError("no Zone found in the .apsimx")
    if _find(zone, lambda n: n.get("Name") == DAILY_REPORT_NAME) is not None:
        raise ValueError("source already has a DailyOutput report; refusing to guess")
    zone.setdefault("Children", []).append({
        "$type": "Models.Report, Models",
        "VariableNames": [f"{expr} as {alias}" if alias != "Date" else expr for expr, alias in DAILY_VARIABLES],
        "EventNames": ["[Clock].EndOfDay"],
        "GroupByVariableName": None,
        "Name": DAILY_REPORT_NAME,
        "ResourceName": None,
        "Children": [],
        "Enabled": True,
        "ReadOnly": False,
    })
    edits.append(f"Added Models.Report '{DAILY_REPORT_NAME}' (EndOfDay) under zone '{zone.get('Name')}' with "
                 f"{len(DAILY_VARIABLES)} variables: " + ", ".join(a for _, a in DAILY_VARIABLES))
    edits.append("Everything else (soil, OilPalm, fertiliser/harvest manager code, Replacements incl. "
                 "MaximumRootDepth 5000, 147 palms/ha, cultivar Dami) is unchanged from the founder's file.")
    Path(dst).write_text(json.dumps(out, indent=2), encoding="utf-8")
    return edits


# ----------------------------------------------------------------------------------------------
# Management levers (cost-benefit scenarios). Only levers that really exist in the founder's model:
#  - N fertiliser: rate and number of applications per year (the manager hard-codes rate by palm age,
#    dates 15 Jan / 15 May / 15 Sep and a 50:50 NO3N:NH4N split; we add a multiplier and a count).
#  - Irrigation: the model exists but nothing applies water; a rule-based top-up manager is ADDED.
# Not levers: harvest interval (hard-coded 14 days), fertiliser form / NO3:NH4 split, P/K (not modelled),
# any leaching-prevention practice other than N rate and N splitting (nothing else in the model).
# ----------------------------------------------------------------------------------------------
import re  # noqa: E402

N_RATE_LINES = ("population * 1.08 / 3.0", "population * 1.2 / 3.0", "population * 0.14 / 3.0",
                "population * 0.3 / 3.0", "population * 0.6 / 3.0", "population * 0.9 / 3.0")
_APP_DAY_RE = re.compile(
    r"if \(\(Clock\.Today\.Month == 1 && Clock\.Today\.Day == 15\) \|\| \s*"
    r"\(Clock\.Today\.Month == 5 && Clock\.Today\.Day == 15\) \|\| \s*"
    r"\(Clock\.Today\.Month == 9 && Clock\.Today\.Day == 15\)\)")


def apply_n_levers(model: dict, n_multiplier: float, from_year: int, applications_per_year: int) -> list[str]:
    """Edit the 'Palm Management' manager code in `model` (a COPY). n_multiplier scales every N rate from
    `from_year` on; applications_per_year (3 = founder: Jan/May/Sep, 6 = Jan/Mar/May/Jul/Sep/Nov, each on the
    15th) splits the SAME annual N into that many equal doses. Fails loudly if the code is not as expected."""
    if applications_per_year not in (3, 6):
        raise ValueError("applications_per_year must be 3 or 6")
    mgr = _find(model, lambda n: n.get("Name") == "Palm Management")
    if mgr is None:
        raise ValueError("no 'Palm Management' manager found")
    code = "\n".join(mgr["CodeArray"])
    for needle in N_RATE_LINES:
        if needle not in code:
            raise ValueError(f"expected fertiliser rate '{needle}' not found: manager code differs from the founder's")
    if not _APP_DAY_RE.search(code):
        raise ValueError("fertiliser application dates not found in the manager code")
    # annual N per palm rate stays as the founder wrote it; per-dose amount = annual x multiplier / doses
    for needle in N_RATE_LINES:
        base = needle.replace(" / 3.0", "")
        code = code.replace(needle, f"Mult() * {base} / Doses()")
    months = ("Clock.Today.Day == 15 && (Clock.Today.Month == 1 || Clock.Today.Month == 5 || Clock.Today.Month == 9"
              + (f" || (Clock.Today.Year >= {int(from_year)} && (Clock.Today.Month == 3 || Clock.Today.Month == 7 || Clock.Today.Month == 11))"
                 if applications_per_year == 6 else "") + ")")
    code = _APP_DAY_RE.sub(f"if ({months})", code)
    code = code.replace("private byte HarvestCounter = 1;",
                        "private byte HarvestCounter = 1;\n        "
                        f"private double Mult() {{ return Clock.Today.Year >= {int(from_year)} ? {float(n_multiplier)} : 1.0; }}\n        "
                        f"private double Doses() {{ return Clock.Today.Year >= {int(from_year)} ? {float(applications_per_year)} : 3.0; }}")
    if "Mult()" not in code:
        raise ValueError("could not insert multiplier")
    mgr["CodeArray"] = code.split("\n")
    return [f"Palm Management code: every N rate multiplied by {n_multiplier} from {from_year} on "
            f"(1.0 before); applications per year {applications_per_year} "
            f"({'15 Jan/May/Sep as the founder' if applications_per_year == 3 else '15 Jan/Mar/May/Jul/Sep/Nov'}), "
            "the same annual N split into equal doses, 50:50 NO3N:NH4N unchanged"]


IRRIGATION_CODE = """using System;
using Models.Core;
using Models.PMF.OilPalm;

namespace Models
{
    [Serializable]
    public class Script : Model
    {
        [Link] Clock Clock;
        [Link] OilPalm OilPalm;
        [Link] Irrigation Irrigation;
        public int StartYear { get; set; }
        public double TriggerFw { get; set; }
        public double AmountMm { get; set; }
        public int MinDaysBetween { get; set; }
        private int since = 1000;

        [EventSubscribe("DoManagement")]
        private void OnDoManagement(object sender, EventArgs e)
        {
            since++;
            // OilPalm.FW at this point is yesterday's value (it is updated later in the day)
            if (Clock.Today.Year >= StartYear && OilPalm.FW < TriggerFw && since >= MinDaysBetween)
            {
                Irrigation.Apply(amount: AmountMm);
                since = 0;
            }
        }
    }
}"""


def add_irrigation_rule(model: dict, from_year: int, trigger_fw: float, amount_mm: float, min_days: int) -> list[str]:
    zone = _find(model, lambda n: n.get("$type", "").startswith("Models.Core.Zone"))
    if _find(zone, lambda n: n.get("$type", "").startswith("Models.Irrigation")) is None:
        raise ValueError("no Irrigation model in the zone")
    zone["Children"].append({
        "$type": "Models.Manager, Models", "CodeArray": IRRIGATION_CODE.split("\n"),
        "Parameters": [{"Key": "StartYear", "Value": str(from_year)}, {"Key": "TriggerFw", "Value": str(trigger_fw)},
                       {"Key": "AmountMm", "Value": str(amount_mm)}, {"Key": "MinDaysBetween", "Value": str(min_days)}],
        "Name": "WaterTopUp", "ResourceName": None, "Children": [], "Enabled": True, "ReadOnly": False})
    return [f"Added manager 'WaterTopUp' (not in the founder's file): from {from_year}, if yesterday's palm water-stress "
            f"factor fw < {trigger_fw} and at least {min_days} days since the last top-up, apply {amount_mm} mm via the "
            "Irrigation model (efficiency 1, runoff allowed)"]
