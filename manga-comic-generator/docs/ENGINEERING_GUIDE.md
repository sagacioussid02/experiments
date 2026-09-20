# Engineering guide: cost, design decisions and self-improvement

Companion to [`LEDGER.md`](LEDGER.md) (the FROM -> TO data, generated from `ledger.json`). Numbers here come from
the project's own usage logs (`python -m tools.cost_report`), not from memory. Costs are list-price estimates
(app/usage.py), not invoices. Dates: everything below was measured on 2026-09-19.

## 1. What the system is

A product-backed comic generator: a physical handmade character (photo + backstory) becomes an episodic manga
whose main character must keep looking like the product.

```
photo --(Claude vision)--> BIBLE --(human edits + approves)--> approved bible
                                   |                                 |
story --(Claude)--> script --(word lint + rewrite)--> script         |
                                                                     v
photo --(gpt-image)--> N sheet candidates --(gpt-5 judge vs photo+bible)--> human picks --> FROZEN SHEET
                                                                     |
                     cover art, panels --(gpt-image, ref = sheet)-----+--> judge --> redraw with reasons (capped)
                                                                     |
                     compositor (Pillow): cover, character file, pages, product back page --> PDF
```

Models (the "agents"): Claude Sonnet 5 writes story/script/bible; `gpt-image-1` draws; `gpt-5` (low effort) judges;
`gpt-5-mini` locates heads for the judge's zoom crop. A human owns two gates: the bible and the sheet.

## 2. What it costs today (measured)

| Run | What changed | Total | Draws | Avg panel draw | Notes |
|---|---|---|---|---|---|
| run1 | raw photo as reference, high fidelity | $1.667 | 10 | $0.163 | input tokens ~10,750/panel |
| run2 | generated sheets, low fidelity | $0.891 | 10 | $0.063 | + $0.259 one-off sheets; story/script reused |
| run3 | + prompt fixes, cover, bibles | $0.868 | 10 | $0.064 | sheets 0.129, cover 0.071, bible 0.031 |
| run4 | + sheet candidates, judge, retries | $2.877 | 25 | $0.068 | QA $0.585, sheets $0.527, 3/10 panels pass |

All runs plus the judge experiments total about **$9.9** by the usage logs (experiments ~$3.6 of that).

**Measured unit costs** (run3/run4):

| Item | Cost | Latency (mean / p95) | Tokens |
|---|---|---|---|
| Panel draw (medium quality) | $0.068 | 21.1s / 25.1s | ~800-1,500 in, 1,056 or 1,584 out |
| Sheet (high fidelity, one-off) | ~$0.13 | not measured | ~7,000 in, ~1,570 out |
| Cover | $0.076 | not measured | ~1,800 in, 1,584 out |
| Judge review (gpt-5, low) | $0.021 | 26.4s | ~2,600 in, ~1,550 out (reasoning) |
| Head locator (gpt-5-mini) | $0.002 | 1.5s | ~46 out |
| Bible extraction (Claude) | ~$0.015 | not measured | ~2,300 in, ~1,050 out |
| Story + script (Claude) | ~$0.036 | not measured | ~3,100 in, ~3,000 out |

**Where the money goes** (run3 episode): images 73%, sheets 15% (one-off), cover 8%, bible 4%, story+script ~4%.
**The Claude text stages are a rounding error; every real lever is on the image and judge side.**

**The two regimes to keep separate:**
- One-off per character: bible + sheets (~$0.50 for a main character with 3 candidates).
- Recurring per episode: story/script ~$0.04, cover ~$0.08, panels ~$0.07 each, plus QA.
- Best case (everything passes first try) is ~$1.03 per 10-panel episode. Run4 was $2.35 per episode: **QA multiplied
  episode cost 3.3x** and only 3 of 10 panels passed (ledger L23).

## 3. Where the time goes

Measured only from run4 (latency was added late). Sequential call time for the 14 draws + 14 reviews in the resumed
run was 686s (11.4 min). The **judge review (26s) is slower than the image draw (21s)** because ~1,500 hidden reasoning
tokens are generated per review. Panels are drawn strictly one after another, so wall-clock equals the sum.

## 4. Design decisions (what, why, alternatives, evidence)

| Decision | Why | Alternative rejected | Evidence |
|---|---|---|---|
| Staged pipeline, each stage persisted to disk | Cheap stages can fail without losing paid image work; resumable | One big prompt / in-memory | runs survived 2 crashes and a credit outage without lost panels |
| Forced tool calls for structured LLM output | Parsed, schema-shaped dicts instead of JSON in prose | Ask for JSON, regex it | pattern in all Claude stages |
| `ImageGenerator` interface + mock backend | Swap vendors; build everything else with zero image spend | Hard-wire OpenAI | mock let layout/PDF be built free |
| Generated **sheet** as the canonical reference, frozen | One clean subject in target style beats a cluttered photo; reusable across episodes (the subscription) | Photo as reference each time | L03: -61% panel cost, better consistency |
| **Bible**: editable, versioned, human-approved | The product is physical; a human must own what "same character" means | Trust the model's description | L12, L15: quality of the spec drives everything |
| Independent judge from a different vendor than the story writer | Fresh eyes; judging its own family's output biases | Claude judges Claude | L11: judge caught real defects labels missed |
| Deterministic word lint before LLM rewrite | Cheap, exact, auditable; LLM only fixes offenders | Ask the writer to behave | L07, L21 |
| Approval gates (bible, sheet) + cost cap | Do not pay downstream for an unapproved spec | Fully automatic | L15 shows what happens when specs conflict |
| Main strict / supporting lenient | Only the product must resemble reality | Same rule for all | cuts checks and retries on side characters |
| Critical vs minor markers | Acceptance criteria must be achievable and ranked | Every marker can fail a panel | L12 |
| Retry with the judge's reasons, capped, stop if no progress | Turn the judge's findings into a prompt correction | Blind re-roll | L13; but retries improved only 3 of 7 (L23) |
| Flat files, `requests`, no queue/DB | Learning-scale project, inspectable state | Postgres, SDKs, workers | revisit at multi-user scale |

## 5. Do the agents "self-improve"? An honest answer

There are **closed feedback loops inside a run** (self-correction) and **none across runs** (learning). Be precise about
which is which:

| Loop | Producer | Critic | Feedback | Stops when | Type |
|---|---|---|---|---|---|
| Script lint | Claude script | regex against the bible | targeted rewrite of offending panels | no forbidden word left, else error | self-correction |
| Sheet best-of-N | gpt-image | gpt-5 vs the real photo | ranks candidates; human picks | human approves | selection |
| Panel redraw | gpt-image | gpt-5 + head crop | the judge's reasons appended to the next prompt | pass, no progress, or retry cap | self-correction |
| Evaluation harness | (offline) | human labels + judge verdicts | changed prompts/rules/config (by us) | we decide | **human-in-the-loop improvement** |

What has genuinely improved the system so far came from the last row: we measured, found a defect in our own process
(noisy labels, vague likeness gate, over-specified bible, stale script), changed one thing and logged FROM -> TO.
The agents did not change themselves. **Nothing learned in episode 1 carries into episode 2 except what a human writes
into the bible.**

What would make it self-improving, in order of payoff (all use data we now record):
1. **Telemetry -> proposed bible edits.** `qa_events.jsonl` says which check fails most. If a marker fails on most
   first attempts, propose "mark minor" or "sheet/bible conflict" to the human, never apply silently.
2. **Health metric with a circuit breaker.** First-attempt pass rate below ~50% after the first few panels means the
   spec is unachievable; stop retrying and surface the conflicting checks (L23).
3. **Per-character memory of retry hints that worked.** Store hints that turned a fail into a pass and pre-apply them.
4. **Cost-aware escalation.** Cheap reviewer first (minimal effort / mini), escalate to the expensive judge only for
   panels that fail or sit near the threshold.
5. **Judge calibration as a regression gate.** Keep the adjudicated 30-panel set; re-run it whenever the judge model
   or prompt changes and refuse the change if accuracy drops.

## 6. Improvement backlog, ranked (with the measurement that decides it)

| # | Change | Expected effect | Basis | How to verify |
|---|---|---|---|---|
| 1 | Resolve spec conflicts before drawing (sheet vs bible eyes, stale script) | Removes most of the $1.46 QA overhead in run4 | L15, L21: eyes = 28% of failures; 4/10 scripts violated the bible | first-attempt pass rate >= 50% on the next comic |
| 2 | Panel quality medium -> low | ~-60% per panel (~$0.068 -> ~$0.02) | output tokens are ~90% of panel cost (L04); low-tier token counts are from provider docs, unverified | run the 30-panel set at low, compare judge pass rate and eyeball |
| 3 | Prefer 1024x1024 canvases | -33% per square panel | 1,056 vs 1,584 output tokens (L17) | layout study: how many panels can be square |
| 4 | Cheaper/faster judge (minimal effort, gpt-5-mini) | -60-80% review cost, lower latency | L22, L09 (more reasoning did not help) | rerun the labelled set, compare catches/false alarms |
| 5 | Concurrent panel drawing | ~3-4x lower wall-clock, same cost | L19 timings | measure 429 rate at 2-4 workers |
| 6 | Verify prompt caching on the judge | unknown; static prefix is already first | L16 | record cached_tokens per call |
| 7 | Batch API for non-urgent episodes | possibly -50% | unverified whether the image endpoints support it | check provider docs, then a pilot |

Priced roughly: #1 attacks ~$1.4/episode of waste, #2 ~$0.5/episode, #4 ~$0.15/episode, #3 up to ~$0.15/episode.
Do #1 first because #2-#4 all reduce the price of work that #1 might remove entirely.

## 7. How to keep this useful (the experiment protocol)

1. **Baseline:** `python -m tools.cost_report <project>` before changing anything.
2. **Change one variable**, on the fixed evaluation set (`tools/judge_eval.py`, labelled set in `data/projects/_labeling/`).
3. **Compare** cost, latency, first-attempt pass rate, flagged panels, and look at images (metrics lie; eyes do not).
4. **Log it:** `python -m tools.ledger add AREA "title" "from" "to" "evidence" measured shipped "lesson"`, then
   `python -m tools.ledger render`. Keep `confidence` honest: *measured* vs *estimated* vs *unverified*.
5. **Adjudicate disagreements** between judge and labels at full resolution (L11); do not trust either alone.

## 8. Known limits of this data

- Small samples: the A/B used 3 panels; the judge set has 27 usable labels; run4 is one comic.
- Labels were made from thumbnails and covered design only; the judge sometimes knew better (4 of 4 spot-checks).
- Two characters, both plush toys. Nothing here says how it behaves on other products.
- Latency includes rate-limit waits and retries; sheet, cover and Claude latency were not recorded before run4.
- "Consistency" is judged by a model plus our eyes; there is no automated likeness metric against the real product.
