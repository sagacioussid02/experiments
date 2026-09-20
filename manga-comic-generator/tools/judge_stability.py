"""Phase 0b: how stable is the judge, and how well does it match human labels?
Runs each panel N times per reasoning-effort setting. Usage: python -m tools.judge_stability [project_id]"""
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.pipeline.judge import DEFAULT_CHECKS, PanelJudge
from app.pipeline.orchestrator import main_characters
from app.storage import load_project
from app.usage import summarize

SETTINGS = [("low", 3), ("medium", 2)]  # (reasoning effort, runs)
COST_STOP = 1.50
# Labels from my own visual review of project 212cf525356e's panels (small sample, not ground truth):
MUST_FAIL = {"p1-1": "nose small/pointy", "p1-2": "fangs", "p1-5": "nose small/pointy", "p2-3": "nose small"}
MUST_PASS = {"p2-2", "p2-4", "p2-5"}  # p2-5 = expression change only (content smile)
# Bruno's measurable design rules -- in Phase 1 these come from the character bible.
BRUNO_CHECKS = [c for c in DEFAULT_CHECKS]
BRUNO_CHECKS[1] = ("Nose: a large rounded-oval, matte black nose about one third of the muzzle width, at every angle "
                   "-- FAIL if it is small, triangular, pointed or shrunk to a dot")

pid = sys.argv[1] if len(sys.argv) > 1 else Path("data/projects/.last_test_project").read_text().strip()
project = load_project(pid)
by_name = {c.name: c for c in project.characters}
strict = [c.name for c in main_characters(project)]
records = []
jobs = []
for effort, runs in SETTINGS:
    for page in project.pages:
        for panel in page.panels:
            label = f"p{page.page_number}-{panel.panel_number}"
            for r in range(runs):
                jobs.append((effort, label, r, panel.image_path, [by_name[n] for n in panel.characters if n in by_name]))


def run(job):
    effort, label, r, image, cast = job
    if summarize(records)["total_cost_usd"] > COST_STOP:
        return job, None
    judge = PanelJudge(reasoning_effort=effort)
    judge.usage_sink = records.append
    review = judge.review(Path(image), cast, [n for n in strict if n in [c.name for c in cast]],
                          checks={"Bruno": BRUNO_CHECKS}, detail=f"{effort} {label} #{r}")
    return job, review


with ThreadPoolExecutor(2) as pool:
    results = list(pool.map(run, jobs))

table = {}
for (effort, label, r, _, _), review in results:
    if review is not None:
        table.setdefault((effort, label), []).append(review)

print("panel   " + "".join(f"{e:>22}" for e, _ in SETTINGS))
labels = [f"p{p.page_number}-{x.panel_number}" for p in project.pages for x in p.panels]
for label in labels:
    cells = []
    for effort, _ in SETTINGS:
        rs = table.get((effort, label), [])
        fails = sum(not r.passed for r in rs)
        cells.append(f"{fails}/{len(rs)} fail")
    tag = "  <- must FAIL" if label in MUST_FAIL else ("  <- must PASS" if label in MUST_PASS else "")
    print(f"{label:8}" + "".join(f"{c:>22}" for c in cells) + tag)

print()
for effort, _ in SETTINGS:
    unanimous = flips = 0
    caught = missed = fp = 0
    for label in labels:
        rs = table.get((effort, label), [])
        if not rs:
            continue
        fails = [not r.passed for r in rs]
        unanimous += len(set(fails)) == 1
        flips += len(set(fails)) > 1
        majority_fail = sum(fails) * 2 > len(fails)
        if label in MUST_FAIL:
            caught += majority_fail; missed += not majority_fail
        if label in MUST_PASS:
            fp += majority_fail
    print(f"[{effort}] self-agreement: {unanimous}/{unanimous + flips} panels unanimous | "
          f"must-fail caught {caught}/{caught + missed} | must-pass wrongly failed {fp}/{len(MUST_PASS)}")
    reasons = Counter(p.split(' -- ')[0].split(': ', 1)[-1][:40] for label in labels for r in table.get((effort, label), []) for p in r.problems())
    print("   most common failing checks:", dict(reasons.most_common(5)))
s = summarize(records)
by_effort = Counter()
for rec in records:
    by_effort[rec.detail.split()[0]] += rec.cost_usd or 0
print("\nQA cost total $%.3f over %d calls; by effort: %s" % (s["total_cost_usd"], s["calls"], {k: round(v, 3) for k, v in by_effort.items()}))
Path("data/projects").joinpath(f"{pid}_judge_stability.json").write_text(json.dumps({f"{e}|{l}": [r.problems() for r in rs] for (e, l), rs in table.items()}, indent=2))
