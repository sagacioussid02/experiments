# Engineering ledger: FROM -> TO

Generated from `docs/ledger.json` by `python -m tools.ledger render`. Every row is a decision with a before/after; `confidence` says how much to trust the numbers, `status` whether it shipped.

## Cost

### L01. Panel reference conditioning: input_fidelity high -> low

- **FROM:** high fidelity: $0.130-0.196 per panel; 6,763-13,398 input tokens (image tokens dominate)
- **TO:** low fidelity: $0.050-0.071 per panel; 521-914 input tokens
- **Delta:** -62% per panel
- **Evidence:** A/B on 3 panels (tools/ab.py): baseline vs low/1024 vs high/512; consistency compared by eye on the same 3 panels
- **Confidence / status:** measured (n=3, one run each) / shipped
- **Lesson:** Image-input cost is set by the fidelity setting and the number of reference images, not by their pixel size.

### L02. Reference photo resolution 1024px -> 512px

- **FROM:** 1024px references: 6,763 / 13,398 input tokens
- **TO:** 512px references: 6,624 / 13,120 input tokens
- **Delta:** -2% (noise)
- **Evidence:** Same A/B, high fidelity in both arms
- **Confidence / status:** measured (n=3) / rejected
- **Lesson:** Shrinking images does not shrink the bill at high fidelity: check the token counter before optimising pixels.

### L03. Raw product photo -> generated character sheet as the panel reference (with low fidelity)

- **FROM:** run1 (photo refs, high fidelity): 10 panels $1.631; whole comic $1.667
- **TO:** run2 (sheet refs, low fidelity): 10 panels $0.632 + one-off sheets $0.259; whole comic $0.891
- **Delta:** -47% whole comic; -61% panel cost
- **Evidence:** project usage records of ece8ba5d2799 vs 242395bfeb3a (same script; run2 excludes the $0.036 story+script)
- **Confidence / status:** measured (n=1 comic each; confounded with L01) / shipped
- **Lesson:** The sheet is a one-off per character and reusable across episodes, so per-episode marginal cost is the panel cost only.

### L04. Where the money goes: input tokens -> output tokens

- **FROM:** run1 panel: ~10,751 input + ~1,420 output tokens; input side $0.107 of $0.163
- **TO:** run2-4 panel: ~760-1,480 input + ~1,420 output tokens; output side ~$0.057 of ~$0.063 (~90%)
- **Evidence:** per-call usage records; output tokens are exactly 1,056 (1024x1024) or 1,584 (1536x1024 / 1024x1536) at medium quality
- **Confidence / status:** measured / observation
- **Lesson:** After the input side was fixed, the remaining lever is what the image model outputs: quality tier and canvas size (see L17, L18).

### L05. Cost visibility: none -> per-call token/cost/latency records with a spend cap

- **FROM:** no per-call accounting; cost discovered from invoices
- **TO:** every call recorded (tokens, images, $, seconds); MAX_COMIC_COST_USD stops generation; usage.json + GET /projects/{id}/usage; qa_events.jsonl per attempt; python -m tools.cost_report
- **Evidence:** app/usage.py, orchestrator._check_budget, tools/cost_report.py. Latency first captured in run4 (2026-09-19): image draw mean 21.1s (p95 25.1s), judge review mean 26.4s, head locator mean 1.5s
- **Confidence / status:** measured / shipped
- **Lesson:** You cannot optimise what you cannot attribute. Latency was only added on 2026-09-19 (after the first four runs), so earlier runs have no timing data.

### L09. Judge reasoning effort: low -> medium, and ensembles of runs

- **FROM:** low: $0.0167/panel (config B)
- **TO:** medium: $0.0294/panel; ensembles A+B, A+C, B+C added no accuracy
- **Delta:** +76% cost, 0 gain
- **Evidence:** same 30-panel evaluation; stability test of 5 runs/panel showed verdicts flip on 20-30% of panels either way
- **Confidence / status:** measured / rejected
- **Lesson:** Paying for more reasoning or more runs did not buy accuracy on subtle visual details; a single low-effort call with a better view of the feature did.

### L12. Bible markers: every marker can fail a panel -> critical vs minor

- **FROM:** run4: panels p1-1..p1-3 each drew 3 times and never passed ($0.61 of images + ~$0.10 of judging); failures were fine details (eye highlight position, stitch pattern, exact heart size)
- **TO:** fine details marked minor. Result on the 7 panels drawn after the change: NONE of the failures were minor features (eye highlight, mouth stitching, heart size, texture); failing checks were eye shape (7), nose (6), nothing-added (4), heart patch (3), limbs (2)
- **Evidence:** qa_events.jsonl of run4 (14 attempts over 7 panels) and bible v4. Note the 3 earlier panels were judged under bible v3 and stay flagged
- **Confidence / status:** measured (n=7 panels) / shipped
- **Lesson:** Over-specified acceptance criteria make a self-correcting loop burn money on things the generator cannot control. Criteria must be achievable and ranked by importance. But the split only removes noise; the dominant failures came from conflicts elsewhere (L15, L21).

### L13. Retry policy: always use all retries -> stop when a redraw does not improve

- **FROM:** up to 3 draws per failing panel regardless of progress
- **TO:** stops as soon as an attempt is not better than the best so far. In run4, 4 of 7 panels stopped at 2 draws instead of 3 (p1-4 [1,5 problems], p2-1 [3,3], p2-2 [2,3], p2-5 [1,3])
- **Delta:** 4 draws avoided = ~$0.27 image + ~$0.09 judge
- **Evidence:** qa_events.jsonl of run4; each avoided draw = ~$0.068 image + ~$0.023 judge
- **Confidence / status:** measured (n=7 panels) / shipped, measuring
- **Lesson:** A retry loop needs a progress test, not just a counter.

### L16. Judge prompt layout for automatic prompt caching

- **FROM:** static content (system prompt, checks, sheet, photo) is already sent before the panel; cached-token counts are not recorded
- **TO:** proposed: record prompt_tokens_details.cached_tokens per call to verify cache hits; keep the static prefix byte-identical
- **Evidence:** code inspection; no measurement yet
- **Confidence / status:** unverified / proposed
- **Lesson:** Verify caching with the usage counter, do not assume it.

### L17. Canvas size per panel: mostly 1536x1024 / 1024x1536 -> prefer 1024x1024 where the layout allows

- **FROM:** non-square canvas: 1,584 output tokens = $0.063
- **TO:** square canvas: 1,056 output tokens = $0.042
- **Delta:** -33% per square panel
- **Evidence:** observed output token counts (medium quality)
- **Confidence / status:** measured token counts; layout impact untested / proposed
- **Lesson:** Layout and cost are coupled: panel shape decides output tokens.

### L18. Image quality tier: medium -> low for panels (keep sheets and cover at medium)

- **FROM:** medium: 1,056 / 1,584 output tokens per panel (~$0.063 with input)
- **TO:** low: roughly 272 / 408 output tokens per the provider's published tiers (unverified) -> ~$0.02 per panel
- **Delta:** ~-60% per panel (estimate)
- **Evidence:** not tested; needs the eval set to check the judge pass rate and a visual review
- **Confidence / status:** unverified estimate / proposed
- **Lesson:** Largest untried lever: output tokens are ~90% of panel cost. Test with the labelled set before adopting.

### L20. Cost share by stage (run3 episode incl. cover): where NOT to optimise

- **FROM:** images 73% ($0.636), sheets 15% ($0.129, one-off), cover 8% ($0.071), bible 4% ($0.031 both characters), story+script ~4% ($0.036, run1 figure)
- **TO:** -
- **Evidence:** project usage of 212cf525356e and 49a87d74663d
- **Confidence / status:** measured / observation
- **Lesson:** Claude text stages are a rounding error; every serious lever is on the image side.

### L23. QA loop economics: cost per comic went up 3.3x

- **FROM:** run3 episode (no QA, no candidates): $0.708 excluding one-off sheets/bible; 10 panels drawn, none checked
- **TO:** run4 episode (candidates + judge + retries): $2.350 excluding one-off sheets ($2.877 total); 25 draws for 10 panels, 3 passed and 7 flagged; QA overhead = 87% of base panel spend; retries improved 3, no change 1, worse 3 (of 7)
- **Delta:** +232% per episode for 3/10 passing
- **Evidence:** python -m tools.cost_report on run4
- **Confidence / status:** measured (one comic) / observation
- **Lesson:** First-attempt pass rate is the health metric of the acceptance criteria: 1 of 7 here means the spec was unachievable (L15, L21), so retries cannot fix it and only multiply cost. Fix the spec first, then retry.

### L24. Per-comic cost model (measured unit costs)

- **FROM:** -
- **TO:** one-off per character: bible ~$0.015; sheet ~$0.13 each (main character 3 candidates = $0.39) + reviews. Per episode: story+script ~$0.036, cover ~$0.076, panel draw ~$0.068 each (10 = $0.68), judge review ~$0.021 + locator ~$0.002 per attempt. Best case (all pass first time): ~$0.68 + $0.24 + $0.11 = ~$1.03 per 10-panel episode
- **Evidence:** tools.cost_report over runs 1-4
- **Confidence / status:** measured unit costs; best case is arithmetic / observation
- **Lesson:** With unit costs known, every lever can be priced before building it: e.g. quality low (L18) saves ~$0.04 x panels; concurrency (L19) saves time, not money.

## Latency

### L19. Panels drawn one at a time -> concurrent drawing with a rate-limit-aware pool

- **FROM:** sequential: each panel = image draw (mean 21.1s, p95 25.1s) + head locator (1.5s) + judge review (mean 26.4s), then retries. Run4 resumed portion: 14 draws + 14 reviews = 686s (11.4 min) of sequential call time
- **TO:** proposed: draw panels concurrently (2-4 workers) and overlap judging with the next draw; est. 3-4x lower wall-clock for the panel stage
- **Evidence:** timings from usage records (seconds field) in run4. Wall-clock estimate is arithmetic, not measured; the judge (26s) is slower than the image draw (21s)
- **Confidence / status:** latency measured; speed-up estimated / proposed
- **Lesson:** The workload is embarrassingly parallel per panel; the constraint is the provider's images-per-minute limit, so measure 429 rate when raising concurrency.

### L22. Judge review: gpt-5 low effort -> cheaper/faster reviewer

- **FROM:** judge review: mean 26.4s, ~1,549 output tokens (mostly hidden reasoning), $0.0209 per review; slower and about 1/3 the price of an image draw
- **TO:** proposed: try reasoning_effort=minimal and gpt-5-mini on the 30-panel labelled set; est. ~$0.004-0.008 per review and much lower latency if accuracy holds
- **Delta:** est. -60-80% cost, -50%+ latency (unverified)
- **Evidence:** usage records of run4 (14 reviews); price table in app/usage.py
- **Confidence / status:** current numbers measured; target unverified / proposed
- **Lesson:** Reasoning tokens are the judge's cost and latency. Earlier tests showed more reasoning did not buy accuracy (L09), so test going down, not up.

## Quality

### L06. Prompts: allow anything -> only listed characters, no text of any kind, no invented claws/fangs

- **FROM:** run2: 4/10 panels flagged for drawn text or an extra character ('CREAK', 'THU', a human head, a shadow figure)
- **TO:** run3: 0/10 flagged (same script)
- **Delta:** 4 -> 0 flagged panels
- **Evidence:** judge config B over the 30-panel set; run2 vs run3 share one script
- **Confidence / status:** measured (n=10 per run) / shipped
- **Lesson:** Negative instructions ('do not draw X') were weakly followed; naming exactly what is allowed plus banning text categories worked. The scene text must not contradict the prompt (the script itself asked for sound effects).

### L07. Script asks for teeth: none -> deterministic forbidden-word check with one targeted rewrite

- **FROM:** script said 'tiny fangs of felt visible'; fangs appeared in that panel in every run
- **TO:** lint flags fang/paw/smile in the existing script (5 hits); a hit triggers one rewrite call, generation stops if a word survives
- **Evidence:** find_forbidden() run against the stored run3 script; unit tests
- **Confidence / status:** measured on the stored script; unverified end-to-end on a fresh script / shipped
- **Lesson:** When the model follows the scene text over the prompt, fix the text upstream. Word lists are per-character but applied per-panel, which over-flags (e.g. Sol's 'mouth' vs Bruno's mouth).

### L08. Judge input: panel shrunk to 1024px, no crop -> full resolution + zoomed head crop

- **FROM:** config A (low effort): $0.0135/panel; catches 2 of 7 human-labelled bad panels, flags 3 of 20 good
- **TO:** config B (low effort + crop): $0.0167/panel; catches 3 of 7, flags 5 of 20
- **Delta:** +1 caught, +2 false alarms, +24% cost
- **Evidence:** 30 panels x 3 configs (data/projects/_labeling/verdicts.json); rules: concrete claims only
- **Confidence / status:** measured, but labels are noisy (see L11) / shipped (B is the default)
- **Lesson:** More pixels on the small feature you are judging helped modestly; small model + crop beat bigger reasoning.

### L10. Judge gate: 'likeness score <= 3 fails' -> only <= 2 fails

- **FROM:** 8 of the 15 panels the human labelled good but the judge flagged were flagged on the vague 1-5 score alone
- **TO:** score only catches gross failures; per-feature checks are the real gate
- **Evidence:** verdicts.json under both rules
- **Confidence / status:** measured / shipped
- **Lesson:** A holistic LLM score is a poor gate; a checklist with concrete observable claims is better.

### L15. Sheet approval: pick the nicest -> check the sheet against the bible first

- **FROM:** run4: the human picked sheet 2 (round soft eyes) although the approved bible says fierce almond eyes; 3 of 3 early panels failed the eye check on all attempts
- **TO:** proposed: score sheet candidates against the bible (the judge already scores them against the photo) and show conflicts before approval, or update the bible deliberately
- **Evidence:** run4: 'Angry-shaped eyes' was the top failing check: 7 of 25 failure instances (28%) in the resumed run, plus all 3 earlier flagged panels. The chosen sheet (round eyes) contradicts the bible (fierce almond eyes) and the panels faithfully copy the sheet
- **Confidence / status:** measured / proposed
- **Lesson:** Two sources of truth (bible vs sheet) must agree before anything downstream is paid for. Gate at the source.

### L21. Script vs bible: lint only when a script is generated -> lint before drawing

- **FROM:** run4 used a script written before the bible: 4 of 10 panels' scene text used words the bible forbids ('paw' x2, 'fist', 'brow'); the judge then failed those panels on 'nothing added' and 'limbs'
- **TO:** generate_images runs the forbidden-word check (and one targeted rewrite) on the stored script before any image is drawn; the extractor is told forbidden_words must cover every 'never' item (fist, eyebrow, hand...)
- **Evidence:** post-mortem of run4 panels; unit test test_script_is_checked_against_the_bible_before_any_image_is_drawn
- **Confidence / status:** cause measured; fix unverified on a live run / shipped
- **Lesson:** Any upstream artifact (script) must be re-validated when the spec it depends on (bible) changes, or the downstream paid stage fails for reasons the loop cannot fix.

## Reliability

### L14. Rate limits and outages: crash / silently accept -> retry with backoff and readable errors

- **FROM:** 429 crashed the image stage; a rate-limited judge call was treated as 'unchecked' and the panel accepted
- **TO:** 429/5xx/SSL retried with Retry-After or exponential backoff; insufficient_quota never retried and shown with the API message; unchecked panels re-checked on retry_flagged
- **Evidence:** two real failures in run4 (one was actually exhausted credits); app/http.py + tests
- **Confidence / status:** measured (incident) / shipped
- **Lesson:** Distinguish 'wait and retry' (rate limit) from 'stop and tell a human' (no credits). Surface the provider's error body.

## Process

### L11. Ground truth: raw human labels -> adjudicated disagreements

- **FROM:** 27 labels made from 640px thumbnails; judge disagreed on 15 'good' panels
- **TO:** spot-check of 4 disagreements at full size: the judge was right on all 4 (claws, fangs, drawn text, a human head)
- **Evidence:** tools/make_label_sheet.py, judge_score.py, manual review
- **Confidence / status:** measured (n=4 checked) / learning
- **Lesson:** Neither the judge nor quick human labels are ground truth. Build the eval set by adjudicating disagreements at full resolution; and label the right thing (design-only labels miss panel-level defects like text).
