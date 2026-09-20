"""Phase 0: run the vision judge over an existing comic's panels and print a pass/fail table.
Usage: python -m tools.judge_baseline [project_id]   (defaults to the last test project)"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.pipeline.judge import PanelJudge
from app.pipeline.orchestrator import main_characters
from app.storage import load_project
from app.usage import summarize

pid = sys.argv[1] if len(sys.argv) > 1 else Path("data/projects/.last_test_project").read_text().strip()
project = load_project(pid)
by_name = {c.name: c for c in project.characters}
strict = [c.name for c in main_characters(project)]
judge = PanelJudge()
records = []
judge.usage_sink = records.append

jobs = []  # (label, image, characters, expected_fail)
for page in project.pages:
    for panel in page.panels:
        cast = [by_name[n] for n in panel.characters if n in by_name]
        jobs.append((f"p{page.page_number}-{panel.panel_number}", panel.image_path, cast, False))
# negative controls: bear-only panels judged as if the cast were Sol -> must fail
sol = [c for c in project.characters if c.name != strict[0]]
solo = [j for j in jobs if [c.name for c in j[2]] == strict][:3]
jobs += [(f"CTRL {j[0]} as {sol[0].name}", j[1], sol, True) for j in solo]


def run(job):
    label, image, cast, _ = job
    return label, judge.review(Path(image), cast, [n for n in strict if n in [c.name for c in cast]], detail=label)


with ThreadPoolExecutor(2) as pool:
    results = list(pool.map(run, jobs))

out = {}
for (label, review), job in zip(results, jobs):
    problems = review.problems()
    out[label] = {"passed": review.passed, "problems": problems, "summary": review.summary,
                  "likeness": {c.name: c.likeness for c in review.characters}}
    mark = "PASS" if review.passed else "FAIL"
    exp = " (expected FAIL)" if job[3] else ""
    print(f"{label:28} {mark}{exp}  likeness={out[label]['likeness']}")
    for p in problems:
        print(f"      - {p}")
real = [v for k, v in out.items() if not k.startswith("CTRL")]
ctrl = [v for k, v in out.items() if k.startswith("CTRL")]
print(f"\nREAL PANELS passed: {sum(v['passed'] for v in real)}/{len(real)}   CONTROLS correctly failed: {sum(not v['passed'] for v in ctrl)}/{len(ctrl)}")
print("QA cost:", json.dumps({k: v for k, v in summarize(records).items() if k in ("total_cost_usd", "input_tokens", "output_tokens", "unpriced_calls")}))
Path("data/projects").joinpath(f"{pid}_judge_baseline.json").write_text(json.dumps(out, indent=2))
