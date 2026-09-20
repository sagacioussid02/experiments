"""Scores cached judge verdicts against your labels. Usage: python -m tools.judge_score path/to/labels.json"""
import json
import sys
from itertools import combinations

from tools.label_common import load_json

labels = {k: v for k, v in json.load(open(sys.argv[1])).items() if v.get("verdict") in ("good", "bad")}
verdicts = load_json("verdicts.json", {})
names = list(verdicts)
# ensembles: fail if ANY member fails (strictest wins)
ens = {}
for r in (2, 3):
    for combo in combinations(names, r):
        ens["+".join(n.split("_")[0] for n in combo)] = {pid: {"passed": all(verdicts[n][pid]["passed"] for n in combo)} for pid in verdicts[combo[0]] if all(pid in verdicts[n] for n in combo)}
bad = [k for k, v in labels.items() if v["verdict"] == "bad"]
good = [k for k, v in labels.items() if v["verdict"] == "good"]
print(f"labelled: {len(good)} good, {len(bad)} bad ({len(json.load(open(sys.argv[1]))) - len(labels)} unsure skipped)\n")
print(f"{'config':22}{'catches bad':>14}{'false alarms':>15}{'accuracy':>10}")
for name, vs in {**verdicts, **ens}.items():
    caught = sum(1 for k in bad if k in vs and not vs[k]["passed"])
    fa = sum(1 for k in good if k in vs and not vs[k]["passed"])
    acc = (caught + (len(good) - fa)) / max(1, len(good) + len(bad))
    print(f"{name:22}{caught:>10}/{len(bad):<3}{fa:>11}/{len(good):<3}{acc:>9.0%}")
print("\nmissed bad panels per config (id: your tags):")
for name, vs in verdicts.items():
    print(" ", name, {k: labels[k].get("tags") for k in bad if k in vs and vs[k]["passed"]})
