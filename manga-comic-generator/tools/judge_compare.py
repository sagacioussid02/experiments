"""Compare cached judge configs on the labelled set: agreement with the baseline config B, catches vs your
labels, flags on 'good' panels, cost and latency per panel.  Usage: python -m tools.judge_compare"""
import json
import re

from tools.label_common import load_json

L = {k: v for k, v in load_json("labels.json", {}).items() if v.get("verdict") in ("good", "bad")}
V = load_json("verdicts.json", {})
bad = [k for k, v in L.items() if v["verdict"] == "bad"]
good = [k for k, v in L.items() if v["verdict"] == "good"]
ADJUDICATED_JUDGE_RIGHT = ["P03", "P22", "P28", "P29"]  # we looked at these at full size: real defects the labels missed


def flagged(cfg, k):  # same rules as the earlier analysis: concrete design problems + panel-level defects
    for p in V[cfg][k]["problems"]:
        if p.startswith("Bruno: likeness"):
            if int(re.search(r"(\d)/5", p).group(1)) <= 2:
                return True
            continue
        if "Markings/patches" in p or "matte" in p.lower():
            continue
        return True
    return False


print(f"{'config':16}{'catches bad':>12}{'flags good':>12}{'caught real (adjud.)':>22}{'agree w/ B':>12}{'$/panel':>9}{'sec/panel':>10}")
for cfg in V:
    done = V[cfg]
    caught = sum(flagged(cfg, k) for k in bad if k in done)
    fa = sum(flagged(cfg, k) for k in good if k in done)
    adj = sum(flagged(cfg, k) for k in ADJUDICATED_JUDGE_RIGHT if k in done)
    both = [k for k in done if k in V.get("B_crop_low", {})]
    agree = sum(flagged(cfg, k) == flagged("B_crop_low", k) for k in both)
    cost = sum(v["cost"] for v in done.values()) / len(done)
    secs = [v.get("seconds") for v in done.values() if v.get("seconds")]
    print(f"{cfg:16}{caught:>9}/{len(bad):<2}{fa:>9}/{len(good):<2}{adj:>19}/{len(ADJUDICATED_JUDGE_RIGHT)}{agree:>9}/{len(both):<2}{cost:>9.4f}{(sum(secs) / len(secs) if secs else float('nan')):>10.1f}")
