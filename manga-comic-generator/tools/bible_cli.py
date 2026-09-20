"""Review/edit/approve character bibles as plain JSON files.
  python -m tools.bible_cli export  [project_id]          -> data/projects/<id>/bibles/<Name>.json
  python -m tools.bible_cli import  <Name> [project_id]   -> loads the edited file (bumps version, needs re-approval)
  python -m tools.bible_cli approve <Name> [project_id]
Project id defaults to the last test project."""
import json
import sys
from pathlib import Path

from app.models import CharacterBible
from app.storage import load_project, project_dir, save_project

args = sys.argv[1:]
cmd = args[0]
name = args[1] if cmd in ("import", "approve") else None
rest = args[2:] if name else args[1:]
pid = rest[0] if rest else Path("data/projects/.last_test_project").read_text().strip()
project = load_project(pid)
folder = project_dir(pid) / "bibles"
character = lambda n: next(c for c in project.characters if c.name.lower() == n.lower())  # noqa: E731

if cmd == "export":
    folder.mkdir(exist_ok=True)
    for c in project.characters:
        if c.bible:
            (folder / f"{c.name}.json").write_text(json.dumps(c.bible.model_dump(), indent=2))
            print(folder / f"{c.name}.json", "| approved:", c.bible.approved, "| version", c.bible.version)
elif cmd == "import":
    c = character(name)
    new = CharacterBible.model_validate_json((folder / f"{c.name}.json").read_text())
    new.version, new.approved = (c.bible.version if c.bible else 0) + 1, False
    c.bible = new
    save_project(project)
    print(f"{c.name}: loaded, now version {new.version}, NOT approved -- run approve when happy")
elif cmd == "approve":
    c = character(name)
    c.bible.approved = True
    save_project(project)
    print(f"{c.name}: bible v{c.bible.version} approved")
