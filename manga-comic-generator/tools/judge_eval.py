"""Runs judge configurations over every panel in the labelling set and caches the verdicts
(resumable). Scoring against human labels is separate (tools.judge_score), so it costs nothing.
Usage: python -m tools.judge_eval"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.pipeline.judge import PanelJudge
from app.usage import summarize
from tools.label_common import BRUNO_CHECKS, WORK, collect_panels, load_json, standard_characters

COST_STOP = 3.0
CONFIGS = {  # name -> PanelJudge kwargs
    "A_plain_low": dict(reasoning_effort="low", native_res=False, face_crops=False),   # what we had
    "B_crop_low": dict(reasoning_effort="low", native_res=True, face_crops=True),
    "C_crop_medium": dict(reasoning_effort="medium", native_res=True, face_crops=True),
    "D_minimal": dict(reasoning_effort="minimal", native_res=True, face_crops=True),
    "E_mini_low": dict(model="gpt-5-mini", reasoning_effort="low", native_res=True, face_crops=True),
    "F_mini_minimal": dict(model="gpt-5-mini", reasoning_effort="minimal", native_res=True, face_crops=True),
}
ONLY = sys.argv[1:]  # optionally run just these configs
panels = load_json("panels.json", None) or collect_panels()
chars = standard_characters()
verdicts = load_json("verdicts.json", {})
costs = load_json("costs.json", {})
records = []
box_cache = {}


def run(job):
    name, panel = job
    if summarize(records)["total_cost_usd"] > COST_STOP:
        return name, panel["id"], None
    judge = PanelJudge(box_cache=box_cache, **CONFIGS[name])
    mine = []
    judge.usage_sink = lambda r: (records.append(r), mine.append(r))
    cast = [chars[n] for n in panel["cast"] if n in chars]
    review = judge.review(Path(panel["image"]), cast, ["Bruno"] if "Bruno" in panel["cast"] else [],
                          checks={"Bruno": BRUNO_CHECKS}, detail=f"{name} {panel['id']}")
    return name, panel["id"], {"passed": review.passed, "problems": review.problems(), "cost": round(sum(r.cost_usd or 0 for r in mine), 5), "seconds": round(sum(r.seconds or 0 for r in mine), 1)}


jobs = [(n, p) for n in CONFIGS if not ONLY or n in ONLY for p in panels if p["id"] not in verdicts.get(n, {})]
print(f"{len(jobs)} judge runs to do ({len(panels)} panels x {len(CONFIGS)} configs, minus cached)", flush=True)
with ThreadPoolExecutor(2) as pool:
    for name, pid, v in pool.map(run, jobs):
        if v is not None:
            verdicts.setdefault(name, {})[pid] = v
            (WORK / "verdicts.json").write_text(json.dumps(verdicts, indent=1))

by = {n: round(sum(v["cost"] for v in vs.values()), 3) for n, vs in verdicts.items()}
print("cost by config (all cached runs):", by, "| this session: $%.3f" % summarize(records)["total_cost_usd"])
for n, vs in verdicts.items():
    print(f"{n:16} flagged {sum(not v['passed'] for v in vs.values())}/{len(vs)} panels")
