"""See the sheet candidates (with the judge's findings) and pick one.
  python -m tools.sheet_cli show   [project_id]
  python -m tools.sheet_cli select <Name> <n> [project_id]   (n is 1-based)
  python -m tools.sheet_cli reset  <Name> [project_id]        (throw away candidates; next run draws new ones)
Project id defaults to the last test project."""
import sys
from pathlib import Path

from app.storage import load_project, save_project

args = sys.argv[1:]
cmd = args[0]
nargs = {"show": 0, "select": 2, "reset": 1}[cmd]
pid = args[1 + nargs] if len(args) > 1 + nargs else Path("data/projects/.last_test_project").read_text().strip()
project = load_project(pid)
find = lambda n: next(c for c in project.characters if c.name.lower() == n.lower())  # noqa: E731

if cmd == "show":
    for c in project.characters:
        state = "approved" if c.sheet_approved else ("WAITING FOR YOUR PICK" if c.sheet_candidates else "no candidates")
        print(f"\n{c.name}{' (main)' if c.main else ''}: {state}" + (f"  -> {c.sheet_image_path}" if c.sheet_image_path else ""))
        for i, cand in enumerate(c.sheet_candidates, 1):
            verdict = "not checked" if not cand.checked else ("no problems found" if not cand.problems else f"{len(cand.problems)} problem(s)")
            print(f"  [{i}] {cand.path}   judge: {verdict}")
            for p in cand.problems:
                print("        -", p[:210])
elif cmd == "select":
    name, n = args[1], int(args[2])
    c = find(name)
    c.sheet_image_path, c.sheet_approved = c.sheet_candidates[n - 1].path, True
    save_project(project)
    print(f"{c.name}: sheet {n} approved -> {c.sheet_image_path}")
elif cmd == "reset":
    c = find(args[1])
    c.sheet_image_path, c.sheet_candidates, c.sheet_approved = None, [], False
    save_project(project)
    print(f"{c.name}: sheets cleared")
