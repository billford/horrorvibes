# v3: more diverse videos, better and less repetitive quotes

Status: approved 2026-11-16. Phases 1 and 2 are implemented on branch `v3-diversity`; phase 3 is not
started. Decisions: pin `gpt-5.6-sol`, build all six phase-2 items, ship style packs.

## Why the output repeats today

All of these come from reading the v2 code. Production history on lola has not been analysed yet.

| # | Cause | Where |
|---|-------|-------|
| 1 | `gpt-4` (2023) via Chat Completions, `temperature 0.7`, `top_p 0.9`. Conservative sampling on an old model gravitates to the canonical lines. | `config.py`, `quotes._call_chat_client` |
| 2 | Only 8 broad themes. "slasher films" always lands on *Halloween* / *Scream* / *Friday the 13th*. | `quotes.themes` |
| 3 | The prompt never tells the model what was already used. Dedup is after-the-fact, so attempts are burned regenerating the same famous lines and runs come back short. The "Random seed: N" in the prompt does nothing. | `build_quote_request` |
| 4 | The movie-cooldown key keeps year and punctuation: `the shining (1980)` != `the shining`, so the 14-day cooldown is trivially bypassed. | `normalize_movie` |
| 5 | Quote dedup is exact-match on `quote + title`. The same line with a different year format, or a near-identical wording, counts as new. | `normalize_quote` |
| 6 | Free-text parsing on `" - "`: a quote containing ` - ` is mis-split; preamble lines are only caught by luck. | `textutil.split_quote_and_title` |
| 7 | No accuracy check: attribution rests entirely on the model's memory. | -- |

On the video side, every run uses one image style suffix, one font and layout, one music anchor, one voice, and
static frames with hard cuts. Each video differs only in its 12 images.

## Phase 1: quote engine (implemented)

**Structured output via the Responses API.** One request returns a JSON array that conforms to a schema:
`quote, title, year, medium (film|tv), speaker, confidence`. There is no more line parsing. Downstream
modules still receive the existing `'QUOTE' - TITLE (YEAR)` strings, so nothing else in the pipeline changes.

**Slot briefs for diversity.** Each run builds one brief per quote by sampling independent axes, with no two
slots sharing a decade+subgenre pair:

- decade: 1920s through 2020s
- subgenre: about 24 (folk horror, body horror, cosmic, J-horror, giallo, found footage, creature feature, gothic,
  anthology TV, ...)
- region: US, UK, Japan, South Korea, Spain, Italy, France, Mexico, ... (weighted toward US/UK, where quote
  recall is most reliable)
- medium: film or TV

The model must answer every slot and may relax region or decade when it has no confident quote. It must never
relax accuracy. This moves the model off the same 30 films far more effectively than temperature does.

**Explicit exclusions in the prompt.** The prompt lists titles still in cooldown and the most recent N used
quotes (bounded by `quotes.exclusion_prompt_limit`), so the model avoids them up front instead of being
filtered afterwards.

**Fixed normalization.**
- Title key: lowercase, year removed, leading article removed, punctuation removed.
- Quote dedup compares quote text only, using a fuzzy ratio (`difflib`, threshold
  `quotes.fuzzy_dup_threshold`, default 0.88).

**Optional web verification** (`quotes.verify: true`). A second Responses call with the `web_search` tool
checks every candidate and returns a verdict per item. Items judged inaccurate or misattributed are dropped. A
corrected year is applied. If the verify call itself errors, candidates are kept and a warning is logged, so an
unattended run never fails on verification.

**Model.** `quotes.model` defaults to `gpt-5.6-sol` (the GPT-5.6 flagship) and is configurable. `quotes.reasoning_effort` defaults to
`low`. `temperature` is now optional and is only sent when set, because reasoning models reject it.

## Phase 2: video diversity (implemented)

Ordered by impact per unit of effort and cost.

1. **Style packs.** A config list of named packs, rotated least-recently-used per run. Each pack sets:
   - image style suffix
   - font and text layout
   - colour grade (ffmpeg `curves` / LUT)
   - music `mood_anchor` and pool
   - narration voice
   - transition type

   Example packs: *Expressionist oil*, *70s grindhouse poster*, *VHS found-footage*, *Woodcut folk horror*,
   *Cosmic void*, *Polaroid evidence*. This adds no new spend and probably gives the biggest perceived
   variety.
2. **Motion.** Ken Burns zoom and pan per frame (`zoompan`), `xfade` transitions between quotes, and a
   grain/vignette overlay chosen by the pack. Static PNGs become a moving short. This costs render time only.
3. **Per-quote scene prompts.** Add a `visual_scene` field to the quote schema: a concrete, original image
   description written by the LLM, which replaces `Visual mood inspired by "<title>": <quote>`. It must not
   depict the film's actual characters or actors (copyright and likeness).
4. **Image model upgrade.** SDXL base is from 2023. FLUX-family models are the current open standard on Apple
   Silicon, but diffusers on MPS is reported unstable for FLUX. The practical route is **mflux** (MLX). This
   needs a bake-off on lola before switching. The OpenAI image fallback should move to the current
   `gpt-image` model.
5. **Narration.** `eleven_v3` with audio tags (`[whispers]`, `[slowly]`) chosen per quote by the LLM, and a
   voice per pack. Optionally use the `/with-timestamps` endpoint for word-synced animated captions (confirm
   v3 timestamp support first).
6. **Episode formats.** Some runs become themed episodes ("Folk Horror Night", "Villains' Last Words",
   "Japanese Ghost Stories", "80s Creature Features") with a variable quote count of 6-12. Themed titles also
   make better YouTube metadata than "12 Bone-Chilling Horror Quotes".

## Phase 3: feedback loop (later)

Pull per-video retention from the YouTube Analytics API and weight style packs and episode formats by
performance.

## Constraints carried forward

- Never reintroduce library music (Content ID; see the curated-upload guard).
- Every external call stays injectable and mocked in tests.
- An unattended run must degrade, never hang or fail on a non-essential step.

## Implementation notes (phase 2)

- `episode.py` plans each run. Style packs and themed formats rotate least-recently-used from
  `episode_history.jsonl`, and the quote count is drawn from 6-12.
- Quotes gain `visual_scene` and `delivery` fields, which drive the image prompts and the v3 audio tags.
- The image chain is `mflux` (FLUX.2 Klein CLI) -> SDXL -> `gpt-image-2` -> gradient.
- Narration uses `eleven_v3` through `/with-timestamps`. On failure it falls back to
  `eleven_multilingual_v2` untagged, then to the default voice.
- Captions are PIL layer images switched by an ffmpeg `overlay` `enable` window. Homebrew ffmpeg has no
  `libass`, so subtitle filters are not an option.
- Motion clips are rendered in parallel and joined with `xfade`. Each non-final clip is extended by the
  transition length so that every transition starts at the next quote's offset, keeping the total equal to
  the sum of the quote durations. Any failure falls back to static assembly.

## Still open

1. ~~Image bake-off on lola.~~ Done 2026-11-16. FLUX.2 Klein 4B took ~19 s per image against ~88 s for
   SDXL, with much better prompt adherence. 9B is gated (non-commercial), so the default is 4B.
2. Audition the pack voices (Brian, George and Lily are premade voices) and confirm v3 returns character
   timings.
3. Phase 3: weight packs and formats by YouTube retention.
