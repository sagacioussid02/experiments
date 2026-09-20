# Roadmap: from "Bruno" to a multi-character, multi-user comic product

Written 2026-09-20. Sizes are relative (S/M/L), not dates. Costs are the measured unit costs in
[`ENGINEERING_GUIDE.md`](ENGINEERING_GUIDE.md) and [`LEDGER.md`](LEDGER.md); anything marked *illustrative* is arithmetic on
those, not market research.

## 1. Where we are

One product (Bruno + Sol) works end to end: photo -> bible -> sheets -> script -> cover/panels (judged) -> PDF with a
character file page and a real-product back page. Gaps that block your goals:

- Characters live **inside a project**, not in a library; one photo per character; grayscale only.
- One-off scripts: no series, no continuity, no scheduling.
- Layout and options are hard-coded (page count, panel templates, fonts, page size).
- Local files, no auth, no queue, no payments, no moderation. A run takes ~5-15 minutes and can hit provider rate limits.
- Quality/cost is not yet dependable: run4 cost $2.35/episode and only 3 of 10 panels passed the judge, because of
  spec conflicts (ledger L15, L21, L23), not because the approach failed.

**Principles that stay true throughout:** every user's character must resemble its reference (approved by the user, or auto-approved with the
best-scoring candidate when they skip review, recorded as `auto`; 100% likeness is not promisable); every paid stage is metered and capped; the image vendor stays behind the
`ImageGenerator` interface; state is inspectable.

## 2. The data model that unlocks requests 1, 2, 3, 4, 6, 7

Today everything hangs off `ComicProject`. Introduce four durable objects, keep the pipeline as is:

```
Account (later) ─┬─ Character (library)      photos[], bible (versioned), sheets per style (bw, colour), approvals
                 ├─ Series                   character_ids[main, supporting...], premise, tone, cadence, style defaults,
                 │                           running summary + list of past episode beats (for continuity)
                 └─ Episode                  series_id, EpisodeSpec (all options), story, script, panels, cover, PDF, usage
Character also carries: approval (manual | auto), confidence signals (below), reonboard_count, owned (purchased slots persist).
EpisodeSpec = pages, panels/page range, page size preset, reading direction, palette (bw|colour), style preset,
              bubble/font style, cover style, tone/genre/theme, language, review-before-draw flag, quality tier
```

One `EpisodeSpec` is the single source of truth for "customise as much as they can": the API, a UI and a scheduler all
just fill it in. Characters and their approved sheets are reused by every episode, which is what makes a subscription
cheap and consistent.

## 3. Phases

| # | Phase | Delivers (your request) | Size | Depends on |
|---|---|---|---|---|
| 0 | Make one episode reliably good and cheap | prerequisite for everything | M | - |
| 1 | Character library + onboarding a new plushie | 1, part of 6 | M | 0 |
| 2 | Series and scheduled episodes | 2 | L | 1 |
| 3 | Customisation: EpisodeSpec, layouts, editing | 3, 7 | L | 1 (2 optional) |
| 4 | Colour option | 4 | M | 1, 3 |
| 5 | Public product: accounts, uploads, jobs, billing | 5, 6, 7 | XL | 1-3 (4 optional) |
| 6 | Scale, cost and self-improvement | ongoing | M | 0, 5 |

### Phase 0: reliable and cheap (do this first)
- **Make sheet and bible one system, not two:** treat the *approved sheet* as the canonical visual reference for drawing and
  judging, and the *photo* as ground truth used at approval time. At approval, run a **reconciliation**: (1) judge the sheet
  against the photo, (2) list every bible marker the sheet visibly contradicts, (3) the user resolves each conflict
  (update the bible to match the sheet, or redraw). After approval the bible is *re-derived from the sheet* so panel checks
  compare like with like. Auto-approve (skipped review) picks the candidate with the fewest conflicts and does the same
  reconciliation automatically, keeping the product-critical markers and relaxing the rest (L15).
- Keep the script-vs-bible check before drawing (done); add a **circuit breaker** that *degrades gracefully*: if first-attempt
  pass rate falls below ~50% after 3-4 panels, stop retrying, finish the episode with the best attempts, flag those panels
  and record the conflicting checks (L23). The user always gets an episode; the breaker protects margin, it never blocks.
- Test cheaper levers on the labelled set: panel quality low, square canvases, minimal-effort or mini judge (L17, L18, L22).
- **Exit:** a fresh 10-panel episode costs <= ~$1.50 including QA, first-attempt pass rate >= 60%, no unflagged text or
  extra characters, judged on the fixed 30-panel set plus the new episode.
- **Status 2026-09-20:** reconciliation, graceful breaker, cheaper judge and lever tests are DONE. Measured on a fresh episode:
  cost **$1.02 (met)**, no unflagged text or extra characters **(met)**, first-attempt pass **30% (not met)**. A what-if with a relaxed
  never-list reached 50%. What remains is genuine generation drift (nose, heart side, sleepy eyes), see ledger L29 and L30.
  Left to do: the owner's OK to split Bruno's never-list, make heart-patch *side* advisory, and apply strict checks to
  every paid character (needs the retry-cost measurement in Phase 1). Ledger: L22-L30.

### Phase 1: character library and onboarding (request 1)
- **Self-serve by design:** the character's *owner* runs onboarding themselves, so it must be an automated pipeline with
  guided user-facing approvals (bible review in plain language, pick one of the sheet candidates), not an operator task.
  Automatic gates before any money is spent: photo quality (blur, one subject, resolution), a moderation check, and a
  "well-known copyrighted character?" check. **Soft gates, no blocking:** the user is shown the bible and sheet candidates and can approve in one click; if they skip,
  generation continues with the automatically chosen best candidate (fewest problems against both the photo *and* the bible,
  so the run4 conflict cannot recur) and the character is marked `auto`-approved. Users can revise later.
- `Character` becomes a stored entity (own folder/table), separate from any comic; several photos per character.
- One command/API flow: upload photos -> draft bible -> you edit/approve -> sheet candidates -> you pick -> ready.
  Everything after that reuses the frozen sheet. Bible edits create a new version; episodes pin the version they used.
- **Every user character is strict** (bible, several sheet candidates, full checks): extra characters are paid for and carry
  the same likeness promise as the main one. "Main" vs "supporting" stays only as story roles (who gets the character file page).
- **Exit:** a second, different plushie (e.g. a bunny) onboarded in <= ~15 min of human time and <= ~$0.60, and an
  episode drawn with both characters. This is the real test that Bruno was not a special case.

### Phase 2: weekly/monthly series (request 2)
- `Series` with premise, cast, tone and cadence. A **story planner** reads the running summary and past beats so the
  next episode continues the world without repeating plots; each episode adds to the summary.
- Scheduler creates an episode on cadence, runs the pipeline under a per-episode budget, and parks it in "ready for
  review" (or auto-publishes for trusted series). Delivery: PDF + web link first, email later.
- Recurring characters keep their frozen sheets; new guest characters go through onboarding.
- **Exit:** 4 consecutive episodes for one series with visible continuity and no repeated premise, each within budget.

### Phase 3: customisation (requests 3 and 7)
- **Defaults: grayscale and 3 story pages.** Everything else is an option, some locked by plan (see Phase 5).
- Implement `EpisodeSpec` end to end: page count, panels per page, splash frequency, page size presets (US comic,
  manga B6/A5, A4, vertical webtoon), reading direction (LTR/RTL), fonts, bubble style, cover style, tone/genre/theme,
  language, quality tier (cheap draft vs final).
- **Human-in-the-loop edits:** review and edit the script and dialogue *before* drawing (the cheapest place to fix
  things), re-roll or lock individual panels, swap the cover.
- Layout engine gets templates beyond the current row/gutter heuristic (LLM-suggested panel sizes were already on the
  DESIGN.md list).
- **Exit:** the same story renders in three page sizes and two reading directions with no code changes, and a user can
  correct a line of dialogue and redraw one panel without regenerating the episode.

### Phase 4: colour vs grayscale (request 4)
- `palette: bw | colour` in `EpisodeSpec`. The bible already stores real colours; colour needs a **colour sheet** per
  character (another ~$0.13 each, cached), colour prompts, and a colour-fidelity check (dominant colours of the character
  region vs the bible's colours, plus the judge).
- Expect colour consistency to be harder than shape consistency; budget for a stricter tier or a "colour palette lock".
- Panel output cost is set by canvas size and quality tier, not colour, so per-panel price should not change; sheets do.
- **Exit:** a colour episode where the main character's palette matches the bible on >= 90% of panels by the check,
  judged against the real photo.

### Phase 5: public product (requests 5, 6, 7)
- **Infrastructure:** managed auth; Postgres; object storage for photos/panels/PDFs; a **job queue with workers**
  (episodes take minutes, so the API returns immediately and streams progress); a **global rate limiter** in front of the
  image API (we already hit 429s with one user; all users share the same provider limits); per-user spend caps.
- **Uploads (request 6):** photo checks (blur, one subject, size), auto-drafted bible, mandatory user approval of bible
  and sheet, storage and deletion policy for user photos.
- **Billing (request 5):** characters and episodes are sold differently. **Extra characters are a one-time $5 SKU** (a
  character slot: bible, sheet, reuse forever). **Episodes** are the recurring part, either pay-per-go or a subscription. Both
  episode options run on a **credit ledger** (1 credit ~ 1 finished panel; covers the panel, its
  checks and capped retries):
  - *Pay per go:* buy credit packs, spend per episode; failed or flagged-and-refused runs refund credits.
  - *Subscription:* a monthly credit allowance (with a rollover rule), optional per-series cadence, higher priority.
  - Payment provider handles cards, invoices, taxes; we only track credits and usage.
- **Plans and entitlements** (proposal, for you to edit). One entitlement table drives what the API and UI allow:

  | Feature | Free trial | Paid |
  |---|---|---|
  | Characters | 1 (photo or original art) | 1 included + extra characters at **$5 each, one-time**; same likeness promise |
  | Backstory, personality, traits | yes | yes |
  | Episodes | 1, lifetime | plan credits or pay-per-go |
  | Pages | 3 (default) | plan limit (e.g. up to 12) |
  | Palette | grayscale | colour (premium, later) |
  | Quality / QA retries | draft tier, capped retries | final tier, plan-based retries |
  | Script review + dialogue editing, panel re-roll, cover restyle | locked | unlocked |
  | Layout, page size, reading direction, fonts | defaults only | unlocked |
  | Series / scheduling | locked | subscription |
  | Watermark | 50% of panels chosen at random (seeded per episode, stored) | none |

  The trial must be **abuse-resistant**: verified email plus **phone verification** (one trial per number, store only a hash),
  a hard per-account spend cap, and the partial watermark. Virtual/VoIP numbers can bypass phone checks; expect to tune this. A trial costs real money (section 4), so do not open it before the draft tier exists.
- **Onboarding guarantee:** if a character's result is poor, the owner may **re-onboard free up to 2 times**; if it still has
  low confidence, the $5 is refunded. *Low confidence* = at least 2 of 3 signals fail: (a) approved sheet still has critical
  problems against the photo, (b) unresolved bible-vs-sheet conflicts remain, (c) first-episode first-attempt pass rate
  is below ~60%. Worst-case exposure per character is ~$1.41 of generation plus the $5 refund. Purchased characters persist
  regardless of subscription status.
- **Moderation and policy** (see risks): screening of uploads and prompts, a terms of service, refund policy, abuse limits.
- **Exit:** ten real users finish onboarding and at least one paid episode each, with cost per user visible on an admin
  page and no run exceeding its budget.

### Phase 6: scale, cost and self-improvement
- Concurrent panel drawing, cheaper judge, caching checks, possibly batch pricing for non-urgent episodes (verify support).
- Telemetry -> proposed bible edits; per-character memory of retry hints that worked; judge calibration as a regression
  gate; a second image vendor behind the same interface (ENGINEERING_GUIDE sections 5-6).

## 4. Unit economics (illustrative; recompute after Phase 0)

Measured: story+script $0.036, cover $0.076, panel draw $0.068, judge review + locator ~$0.023, one-off per character
~$0.50 (bible + 3 sheet candidates). With `r` = average extra attempts per panel and N panels:

`episode cost = 0.112 + N x (0.068 + 0.005) x (1 + r)`   (review now ~$0.005 with the mini judge)

| r (avg extra attempts) | 10-panel episode | Note |
|---|---|---|
| 0 | ~$0.84 | everything passes first time (best case) |
| 0.3 | ~$1.06 | Phase 0 measured: $1.02 |
| 1.5 | ~$1.94 | run4 was $2.35 with the old, costlier judge |

**With the chosen default (3 story pages, ~4 panels per page = ~12 panels):**

| r | 12-panel episode | + onboarding one main character (~$0.50) = one free-trial user |
|---|---|---|
| 0 | ~$0.99 | ~$1.46 |
| 0.5 | ~$1.43 | ~$1.90 |

*Illustration:* if 5% of trial users convert, each paying customer has already cost ~$29-38 in trials. That is why the
trial needs a draft tier (low quality: a draw costs $0.024 instead of $0.068, measured; a 12-panel trial episode is ~$0.5 plus ~$0.47 onboarding) and hard limits.
The PDF also gains a cover, and the character file and product pages should appear only in a character's first episode.

**Extra character SKU (illustrative):** onboarding cost is ~$0.47 (bible ~$0.015, 3 sheet candidates ~$0.39, their reviews ~$0.06)
against a $5 price, so ~$4.5 gross before payment fees, support and free re-rolls. Two hidden costs to watch: a sheet re-roll
is another ~$0.45, so cap free re-rolls; and every extra *strict* character adds checks to every panel it appears in, which
raises `r` (the retry rate) for later episodes. That extra cost lands on the episode subscription, so measure it in Phase 1.

*Illustration only:* at a 3x markup, a 10-panel episode would sell for ~$3-4.50 and a weekly series (about 4-5 episodes a
month) would need roughly $19-25/month to cover its cost-to-serve plus overhead. Reducing panel cost (quality tier,
canvas size, cheaper judge) is worth more than any pricing tweak, and the plan design should **cap retries per plan** so
`r` cannot run away. A cheap "draft" tier (low quality, no retries) could cost about a third of a final episode
(estimate, unverified).

## 5. Risks and things that are not ours to wave away

- **Likeness promise:** no image model guarantees an identical character. Market it as "faithful, approved by you",
  keep the approval gates, and offer redraw/refund rules.
- **User-uploaded characters:** copyrighted characters, real people and minors are the serious cases. Launching with
  "handmade toys, objects and your own original characters", blocking real-person likeness, plus moderation and a
  takedown path is the conservative start. This needs legal review; it is not legal advice.
- **Vendor concentration:** one image API sets your cost, quality and rate limits. Keep the interface, and prove a second
  backend before you depend on volume.
- **Latency and rate limits:** minutes-long jobs need async UX; shared provider limits need a queue and backoff.
- **Privacy:** user photos are personal data; define retention, deletion and who can see them.
- **Provider terms:** confirm the image provider's terms allow commercial resale of outputs.
- **Cost variance:** the retry loop can multiply cost (run4 was 3.3x); budgets and the circuit breaker are product
  features, not nice-to-haves.

## 6. Order and what can run in parallel

Do 0 -> 1 -> 2/3 in that order for the core value: reliable episodes, then a library of characters, then series and
customisation (2 and 3 can overlap). Colour (4) can wait until 3's `EpisodeSpec` exists. The public product (5) is the
largest and riskiest; its infrastructure (auth, DB, queue) can start after Phase 1 if you want early users, but don't
open sign-ups before Phase 0's cost and quality targets hold and moderation exists.

## 7. Decisions (2026-09-20) and what they change

| Decision | Consequence |
|---|---|
| The character's **owner** onboards their own character; make it a pipeline | Self-serve and automated from day one; onboarding cost (~$0.50) is ours, inside trial and plan pricing |
| Users upload **toys and main original characters**; extra characters by plan | Entitlements table (Phase 5); the real-product back page becomes optional for original art |
| Extra characters: **$5 one-time**; future episodes on a separate subscription | Character slot SKU + episode credits (pay-per-go or subscription); cap free sheet re-rolls |
| **Same likeness promise** for extra characters | All user characters run the strict pipeline; more strict characters per episode raises retry cost; measure it |
| **Soft gates, never block** (skipped review -> auto-approve best candidate, continue) | Approval quality depends on a good automatic choice; circuit breaker degrades gracefully; `auto` approvals are tracked |
| **Free trial**, advanced options locked, small allowance | Draft tier, hard caps, phone verification and partial watermark come before opening sign-ups |
| **Phone verification** for the trial | One trial per number, hashed storage; VoIP numbers can slip through |
| **Watermark on selected panels only** | Compositor stamps chosen panels; cheaper to bypass than a full-page mark, by design |
| **Grayscale and 3 pages are the defaults** | `EpisodeSpec` defaults; colour is a later premium option (Phase 4) |
| **No physical print for now** | No bleed or print-resolution work; page-size presets are digital only |

**Resolved 2026-09-20 (second round):**
- Watermark: **50% of panels, chosen at random** per episode (seeded and stored so re-renders are stable), stamped inside the art area.
- **2 free re-rolls/re-onboards** per character, then refund if confidence stays low (definition above).
- **Purchased characters persist** after a subscription is cancelled.

**Still open:**
1. Confirm the low-confidence definition (2 of 3 signals) and the ~60% pass-rate threshold once we have data from Phase 0/1.
2. Should a re-onboard be allowed with a **new photo**? (Likely yes: bad photos are a common cause; it is also the cheapest fix.)
3. Should the confidence score be shown to the user, and does it gate the refund automatically or go to a human first?
