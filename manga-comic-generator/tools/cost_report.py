"""Cost / token / latency report for a project.  Usage: python -m tools.cost_report [project_id]
Reads usage records (project.json) and the per-attempt QA log (qa_events.jsonl). Latency is only
available for calls made after 2026-09-19 (earlier runs show '-')."""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, quantiles

from app.storage import load_project, project_dir

pid = sys.argv[1] if len(sys.argv) > 1 else Path("data/projects/.last_test_project").read_text().strip()
p = load_project(pid)


def pct95(xs):
    return quantiles(xs, n=20)[-1] if len(xs) >= 2 else (xs[0] if xs else None)


def fmt(x, spec=".1f"):
    return "-" if x is None else format(x, spec)


print(f"Project {pid}\n")
print(f"{'stage':8}{'calls':>6}{'in tok':>9}{'out tok':>9}{'$':>9}{'$/call':>9}{'sec mean':>10}{'sec p95':>9}")
by = defaultdict(list)
for r in p.usage:
    by[r.stage].append(r)
total = 0.0
for stage, rs in by.items():
    cost = sum(r.cost_usd or 0 for r in rs)
    total += cost
    secs = [r.seconds for r in rs if r.seconds is not None]
    print(f"{stage:8}{len(rs):>6}{sum(r.input_tokens for r in rs):>9}{sum(r.output_tokens for r in rs):>9}{cost:>9.3f}{cost / len(rs):>9.4f}{fmt(mean(secs) if secs else None):>10}{fmt(pct95(secs)):>9}")
print(f"{'TOTAL':8}{len(p.usage):>6}{sum(r.input_tokens for r in p.usage):>9}{sum(r.output_tokens for r in p.usage):>9}{total:>9.3f}")
unpriced = sum(1 for r in p.usage if r.cost_usd is None)
if unpriced:
    print(f"  ({unpriced} unpriced calls not in the total)")

panels = [(pg.page_number, x) for pg in p.pages for x in pg.panels if x.image_path]
if panels:
    per_image = [r.cost_usd or 0 for r in by.get("images", [])]
    n_panels = len(panels)
    draws = len(per_image)
    avg_draw = mean(per_image) if per_image else 0
    print(f"\nPanels: {n_panels} drawn with {draws} draws ({draws - n_panels} extra) | avg draw ${avg_draw:.4f}")
    attempts = Counter(x.qa_attempts for _, x in panels)
    print("QA attempts per panel:", dict(sorted(attempts.items())), "| passed:", sum(1 for _, x in panels if x.qa_passed), "| flagged:", sum(1 for _, x in panels if x.qa_passed is False), "| unchecked:", sum(1 for _, x in panels if x.qa_passed is None))
    wasted_draws = sum(max(0, x.qa_attempts - 1) for _, x in panels)
    qa_cost = sum(r.cost_usd or 0 for r in by.get("qa", []))
    print(f"Redraw spend: ~${wasted_draws * avg_draw:.3f} on {wasted_draws} extra draws; judging spend ${qa_cost:.3f}; "
          f"QA overhead = {100 * (wasted_draws * avg_draw + qa_cost) / max(0.001, sum(per_image)):.0f}% of base panel spend")
    flagged_spend = sum(max(0, x.qa_attempts - 1) * avg_draw for _, x in panels if x.qa_passed is False)
    print(f"  of which redraws that still ended flagged (no payoff): ~${flagged_spend:.3f}")

log = project_dir(pid) / "qa_events.jsonl"
if log.exists():
    events = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    first = [e for e in events if e["attempt"] == 1]
    print(f"\nQA log: {len(events)} attempts over {len(first)} panels; first-attempt pass rate {sum(1 for e in first if e['problem_count'] == 0)}/{len(first)}")
    fails = Counter(prob.split(":")[0] for e in events for prob in e["problems"])
    print("Most frequent failing checks (all attempts):", dict(fails.most_common(6)))
    helped = [(e0["problem_count"], e1["problem_count"]) for e0 in events for e1 in events if e0["panel"] == e1["panel"] and e1["attempt"] == e0["attempt"] + 1]
    if helped:
        print(f"Retries: {sum(1 for a, b in helped if b < a)} improved, {sum(1 for a, b in helped if b == a)} no change, {sum(1 for a, b in helped if b > a)} worse (of {len(helped)})")
