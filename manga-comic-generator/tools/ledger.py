"""FROM -> TO ledger of engineering decisions and their measured effect.
  python -m tools.ledger render                      -> docs/LEDGER.md (from docs/ledger.json)
  python -m tools.ledger add AREA "title" "from" "to" "evidence" [confidence] [status] ["lesson"] ["delta"]
Areas: cost | quality | latency | reliability | process. Add an entry whenever you change something
that has a measurable before/after, and update `to`/`confidence` when the measurement lands."""
import json
import sys
from datetime import date
from pathlib import Path

DATA = Path("docs/ledger.json")
OUT = Path("docs/LEDGER.md")
ORDER = ["cost", "latency", "quality", "reliability", "process"]


def render() -> None:
    entries = json.loads(DATA.read_text())
    lines = ["# Engineering ledger: FROM -> TO", "",
             "Generated from `docs/ledger.json` by `python -m tools.ledger render`. Every row is a decision with a before/after; "
             "`confidence` says how much to trust the numbers, `status` whether it shipped.", ""]
    for area in ORDER:
        rows = [e for e in entries if e["area"] == area]
        if not rows:
            continue
        lines += [f"## {area.capitalize()}", ""]
        for e in rows:
            lines += [f"### {e['id']}. {e['title']}", "",
                      f"- **FROM:** {e['from']}", f"- **TO:** {e['to']}"]
            if e.get("delta"):
                lines.append(f"- **Delta:** {e['delta']}")
            lines += [f"- **Evidence:** {e['evidence']}", f"- **Confidence / status:** {e['confidence']} / {e['status']}",
                      f"- **Lesson:** {e['lesson']}", ""]
    OUT.write_text("\n".join(lines))
    print(f"wrote {OUT} ({len(entries)} entries)")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "render":
        render()
    elif cmd == "add":
        a = sys.argv[2:] + [""] * 9
        entries = json.loads(DATA.read_text())
        n = 1 + max(int(e["id"][1:]) for e in entries)
        entries.append({"id": f"L{n:02d}", "date": str(date.today()), "area": a[0], "title": a[1], "from": a[2], "to": a[3],
                        "delta": a[8], "evidence": a[4], "confidence": a[5] or "unverified", "status": a[6] or "proposed", "lesson": a[7]})
        DATA.write_text(json.dumps(entries, indent=1))
        render()
