"""Draw a few existing panels at a different quality / canvas size and judge them, to price the draft tier.
Usage: python -m tools.quality_probe <quality> [square]    e.g. python -m tools.quality_probe low square
Uses the last test project's panels, characters and sheets; writes images to data/projects/_probe/."""
import sys
from pathlib import Path

from app.pipeline.image_generator import OpenAIImageGenerator
from app.pipeline.judge import PanelJudge
from app.storage import load_project
from app.usage import summarize

quality = sys.argv[1]
square = "square" in sys.argv[2:]
project = load_project(Path("data/projects/.last_test_project").read_text().strip())
out = Path("data/projects/_probe")
out.mkdir(exist_ok=True)
gen = OpenAIImageGenerator(quality=quality)
judge = PanelJudge()
records = []
gen.usage_sink = records.append
judge.usage_sink = records.append
by_name = {c.name: c for c in project.characters}
picks = [(1, 1), (1, 2), (1, 4), (2, 1), (2, 3), (2, 5)]
print(f"quality={quality} canvas={'square 1024' if square else 'as laid out'} | medium-quality result in brackets\n")
for pg, pn in picks:
    panel = project.pages[pg - 1].panels[pn - 1]
    panel = panel.model_copy(update={"retry_hint": "", "orientation": "square" if square else panel.orientation})
    path = out / f"{quality}{'_sq' if square else ''}_p{pg}-{pn}.png"
    gen.generate_panel(panel, project.characters, path)
    draw = records[-1]
    cast = [by_name[n] for n in panel.characters if n in by_name]
    strict = [c.name for c in cast if c.main]
    problems = judge.review(path, cast, strict, detail=path.stem).problems()
    prev = project.pages[pg - 1].panels[pn - 1]
    print(f"p{pg}-{pn} {panel.orientation:9} out_tokens={draw.output_tokens:>5} ${draw.cost_usd:.4f} {draw.seconds:5.1f}s | judge problems: {len(problems)} "
          f"[medium: {'pass' if prev.qa_passed else 'flag(' + str(len(prev.qa_problems)) + ')'} after {prev.qa_attempts} tries]")
    for p in problems[:3]:
        print("     -", p.rsplit(" -- ", 1)[-1][:150])
draws = [r for r in records if r.stage == "images"]
print(f"\ndraw avg ${sum(r.cost_usd for r in draws) / len(draws):.4f}, {sum(r.seconds for r in draws) / len(draws):.1f}s; total incl. judging ${summarize(records)['total_cost_usd']:.3f}")
