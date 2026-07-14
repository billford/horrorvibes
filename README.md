For details about this project, check out: [https://billford.io/@billfordx/vibing-my-way-to-horror-happiness-509432505faf](https://billford.io/vibing-my-way-to-horror-happiness-509432505faf)

# Horror Movie Quote Video Generator (v2)

A Python application that creates YouTube Shorts-style videos from horror movie quotes, with AI-generated
expressionist artwork and AI-generated meditative horror music, designed to run unattended on a schedule.

This is the v2 rewrite (branch `v2-ai-pipeline`). The original single-file script (`horror-gen.py`, gradient
backgrounds + static audio picker, CLI-only) still exists in this branch for reference during the transition
but is superseded by the `horrorvibes/` package described below.

## What changed from v1

- **Backgrounds**: gradient fills -> locally-generated Stable Diffusion images (Apple Silicon, via `diffusers`
  on the `mps` backend), with automatic fallback to the OpenAI Images API, then to the old gradient generator
  as a last resort. Every image's actual source backend is logged.
- **Music**: static file picker from `./audio` -> AI-generated meditative horror ambient music per run, either
  locally via Stable Audio Open or via the ElevenLabs Music API (your choice, each falls back to the other),
  sized to the whole video (not per-quote), with a random curated file from `./audio` as the final fallback.
- **Config**: CLI flags only -> `config.yaml` (see `config.sample.yaml`). The CLI now only exposes `--quotes`,
  `--dry-run`, `--force`, and `--config`.
- **Code**: one 870-line script -> a modular package (`horrorvibes/`) with typed exceptions instead of
  `sys.exit()`, real logging instead of `print()`, and a full pytest suite. `pylint` and `bandit` both run
  clean (see `pyproject.toml`).
- **Automation**: designed to run unattended via `launchd` on a Mac (see `com.billford.horrorvibes.plist`),
  every other day, including the YouTube upload step -- with a macOS notification if a run fails.

## Architecture

```
config.yaml -> run.py -> horrorvibes/orchestrator.py
                              |
     ---------+--------+--------+-----------+-----------+
     v         v        v        v           v           v
  quotes.py  imagegen.py  musicgen.py  compositor.py  video.py  publish.py
  (OpenAI     (local SD -> (ElevenLabs  (text-on-      (ffmpeg    (YouTube
   chat API)   OpenAI ->    Music API -> image,         assembly)  upload,
               gradient)    curated     scrim only                 refresh-
                            file)        behind text)               token only)
```

Every module is independently importable and independently testable; the orchestrator owns retry/fallback
policy and logging. See `tests/` for the full suite (mocks every external call -- no network, no real model
inference, no real uploads run in CI).

## Requirements

- Python 3.11+
- FFmpeg (for video assembly) -- install separately, see `INSTALL.md`
- An OpenAI API key (quote generation, and the image-fallback path)
- An ElevenLabs API key, if using `music.backend: elevenlabs` (see **Music generation** below for costs)
- ~64GB RAM recommended for local SDXL-class image generation and/or local Stable Audio Open music generation
  (developed against an M1 Ultra Mac Studio); lighter hardware can fall back to the `openai`/`gradient` image
  backends and the `elevenlabs`/`curated_file` music backends instead

## Quick Start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp env.sample .env            # fill in OPENAI_API_KEY (and ELEVENLABS_API_KEY if using elevenlabs music)
cp config.sample.yaml config.yaml   # adjust to taste -- config.yaml already ships with sane defaults

python3 run.py --dry-run      # full pipeline, never uploads -- good first run
python3 run.py                # full pipeline, uploads to YouTube if publish.youtube_upload: true
```

### CLI flags

- `--config PATH`: use a config file other than `./config.yaml`
- `--quotes N`: override `run.quote_count` for this invocation
- `--dry-run`: run the full pipeline but never upload to YouTube
- `--force`: ignore the every-other-day cadence guard and run now

Everything else (image/music backends, prompts, fonts, privacy status, logging, cadence) lives in
`config.yaml` -- see `config.sample.yaml` for the full annotated list of settings.

## Quote accuracy

Quote generation still goes through the same OpenAI chat model as v1 (see Non-Goals) -- what changed is the
prompt and `quotes.temperature` (0.7, down from the API's 1.0 default). A chat model asked for "impactful,
unique, non-overused" horror quotes at high temperature has real incentive to invent a plausible-sounding
line or misattribute a real one to the wrong film. The system prompt now explicitly tells the model accuracy
matters more than novelty, and to pick a different, well-documented quote rather than guess at one it isn't
confident about; the lower temperature makes it lean on what it actually knows rather than get creative. This
narrows but doesn't eliminate the risk -- there's no external quote database cross-check here, so a wrong
attribution is still possible. `quotes.py`'s history/dedup file gives you a durable record of exactly what was
generated per run if you want to spot-check attributions after the fact.

## Image generation

`image.backend` in config.yaml picks where the fallback chain *starts*; it always continues
`local_sd -> openai -> gradient` from there (so `backend: openai` skips local SD entirely and starts at
OpenAI; `backend: gradient` never calls an API at all). The SD prompt is built from the quote's mood + movie
title + a fixed "expressionist horror art" style suffix -- it deliberately never asks the model to render the
quote text itself (SD is unreliable at in-image typography; the words are composited separately, see
`compositor.py`).

**Model choice**: `config.sample.yaml` ships with `stabilityai/sdxl-turbo` (few-step, fast -- a run of
9-12 images should take a few minutes on an M1 Ultra, leaving headroom for `ffmpeg` encoding running
concurrently). If you want higher fidelity at the cost of speed, `stabilityai/stable-diffusion-xl-base-1.0`
with a higher `sd_steps` is a reasonable swap -- do a quick visual bake-off on your own hardware before
committing, per the original design spec's open question on this.

**Testing**: `tests/test_imagegen.py` covers prompt construction and the fallback chain's control flow with
injected fake backends -- it never loads a real model. Real local inference is not exercised by pytest (too
slow/heavy for CI); use `python3 scripts/smoke_test_imagegen.py "'A quote.' - A Movie (Year)"` to manually
eyeball actual output quality/timing after changing the model or prompt.

## Music generation

`music.backend` in config.yaml picks where the fallback chain *starts* -- `local` or `elevenlabs` -- and it
always falls through to the other AI backend before giving up and using a random curated file from
`music.fallback_dir` (default `./audio`) as the last resort. Every fallback is logged as a warning, since a
silently-degraded "generated" track that's actually last year's MP3 again is exactly the kind of thing that
should show up in logs, not just in output. Both AI backends share the same prompt: a fixed anchor phrase
(keeps the mood consistent run-to-run) plus a random subset of mood descriptors from `music.mood_pool` (varies
the texture).

**Local (`music.backend: local`)**: [Stable Audio Open](https://huggingface.co/stabilityai/stable-audio-open-1.0)
via `diffusers`, same pattern as the local image backend -- free, runs on the M1 Ultra's `mps` backend. It's
purpose-built for ambient/sound-design audio rather than structured songs, which fits "meditative horror
ambient" well. The model natively caps out at well under a full run's duration, so `LocalMusicBackend`
generates several `music.local_segment_sec`-long segments and crossfades them together with `ffmpeg`
(`music.local_crossfade_sec` overlap) to reach the target length -- seamless for continuous drone/ambient
texture, which is the only kind of output this prompt asks for.

**ElevenLabs (`music.backend: elevenlabs`)**: calls the
[ElevenLabs Music API](https://elevenlabs.io/docs/api-reference/music) once per run, sized to the whole
video's duration. **Costs real money, not a repurposed existing subscription**: as of this writing, priced at
**$0.15/minute** (~900 credits/minute on subscription plans). At 12 quotes x 10s = 120s (2 minutes) per video,
and roughly 15 runs/month on an every-other-day cadence, that's about **$4.50/month**, or ~27,000 credits/month
-- confirm current pricing at https://elevenlabs.io/pricing/api before enabling this. You'll need a separate
`ELEVENLABS_API_KEY`, and note that ElevenLabs lets you cap an individual key's own credit quota separately
from your account's overall balance (a useful blast-radius limit, but easy to forget you set it to 0 on a
freshly-created key -- check the key's own limit in the dashboard if you get a `quota_exceeded` 401 despite
having account credits).

**Which one should be primary?** That's a call worth making by ear -- generate a track from each
(`scripts/smoke_test_musicgen.py` after toggling `music.backend`) and listen for which one actually sounds
scarier/better for your videos before deciding. Either way the other one covers for it automatically.

**Testing**: `tests/test_musicgen.py` covers duration math, prompt-pool sampling, retry/backoff, segment/
crossfade planning, and fallback ordering for both chain directions -- all with fakes, no real network calls
or model inference. Use `python3 scripts/smoke_test_musicgen.py` to manually generate one real track with
whatever `music.backend` is currently set.

## Voiceover narration (experimental, opt-in)

`voiceover.enabled: true` reads each quote aloud via the ElevenLabs text-to-speech API in a configurable
voice (`voiceover.voice_id`, defaults to "Clyde" -- audition others at
[elevenlabs.io/voice-library](https://elevenlabs.io/voice-library) and swap freely), mixed into the
background music track with the music **ducking** (quieting) during each narration clip's exact known
window, then restoring to full volume in the gaps between quotes.

Ducking is keyed to each narration's actual timing (`ffmpeg`'s `volume` filter with a per-window `enable`
expression), not a signal-level threshold -- an earlier sidechain-compression approach looked reasonable on
paper but a real listen showed narration buried under the music: its threshold was calibrated against
narration's own loudness (~0.02-0.05 RMS), which sits right at the chosen cutoff, while the music's loudness
(~0.18-0.2 RMS) was well above it, so the compressor barely engaged. Since we already know exactly when each
narration plays, ducking those known windows directly is simpler and actually reliable.

- One narration clip per quote, generated independently -- a single quote's narration failing (network
  blip, rate limit) skips just that quote's narration rather than failing the run; if every quote's
  narration fails, the plain music track is used as-is.
- Uses the same `ELEVENLABS_API_KEY` as `music.backend: elevenlabs` -- this is additional spend on top of
  whichever music backend you're using, not a separate subscription.
- Tunable via `voiceover.duck_volume` (music's linear volume, 0-1, during narration) and
  `voiceover.narration_gain` (narration's own volume boost) if the default mix doesn't sit right for your
  content. `voiceover.speed` (ElevenLabs' 0.7-1.2 range) controls delivery pace -- defaults to 0.75 for a
  slower, more menacing read.

**Quotes don't overlap, even with slow narration**: each quote's on-screen duration stretches to fit its own
narration length (plus `voiceover.narration_pad_sec`, default 1s of breathing room) if that narration runs
longer than the nominal `run.duration_per_quote_sec` -- a real bug caught by an actual full run: every quote
got a fixed-length slot regardless of how long its narration actually took to say, so a long narration
(especially at a slower `voiceover.speed`) bled into the next quote's. `orchestrator._compute_quote_schedule`
computes each quote's actual duration and cumulative start offset up front; those flow through to the video's
per-frame timing (`video.assemble_video` takes a duration *per frame*, not one uniform value), the ducked
mix's narration placement, and the music track's total generated length (`musicgen.generate_music`'s
`duration_sec` override) so nothing is sized to the old nominal total once narration has stretched it.

**Testing**: `tests/test_voiceover.py` covers the ducked-mix `ffmpeg` command construction (single- and
multi-narration cases, using each narration's actual computed start offset, not a fixed
`quote_index * duration_per_quote_sec` slot) and per-quote fallback behavior. `tests/test_orchestrator.py`
covers the schedule computation directly (nominal duration, short narration, long narration stretching a
slot, a failed narration falling back to nominal) plus an end-to-end regression test asserting the stretched
durations actually reach `video.assemble_video` and `musicgen.generate_music`. All with fakes, no real
network calls, model inference, or `ffmpeg` invocations. `orchestrator.py` falls back to the plain music
track if voiceover is disabled, every narration fails, or the mix step itself fails, also covered.

## Unattended YouTube upload

Uploads use **refresh-token auth only** -- `publish.py` never falls back to opening a browser
(`InstalledAppFlow.run_local_server()`), since an unattended `launchd` run has no browser to open. If the
refresh token is missing or refresh fails, the run logs an error and skips the upload rather than hanging; the
video stays available locally either way.

**One-time setup** (do this manually, before enabling the launchd job):

1. Set up a Google Cloud Project with the YouTube Data API v3 enabled, download the OAuth client secrets as
   `client_secret.json` in the project root.
2. Run the pipeline once interactively (e.g. `python3 run.py --dry-run`, or a small one-off script that
   calls `horrorvibes.publish.get_unattended_credentials` after an interactive
   `InstalledAppFlow.run_local_server()` grant) to produce `token.json`. This step needs a browser; every run
   after it does not.
3. Confirm `token.json` contains a refresh token and `publish.py` can refresh it without your involvement.

**Privacy status**: `publish.privacy_status` in config.yaml defaults to `private`. Since unattended runs
publish without anyone reviewing each video first, we recommend staying on `private` until you've watched a
few unattended runs' worth of output, then flipping to `unlisted` or `public` once you trust the pipeline --
this is your call to make in config.yaml, not something baked into the code.

## Video metadata and the quote catalog

Every upload gets a content-specific title, description, and hashtags (`horrorvibes/metadata.py`) built from
that run's actual quotes -- not a fixed generic blurb. The title names a few of the featured films (e.g. "12
Bone-Chilling Horror Movie Quotes | Halloween, Scream & More"), the description lists every quote with its
film, and hashtags combine generic horror tags with one per featured movie.

Separately, `run.video_catalog_path` (default `./video_catalog.jsonl`) gets one append-only JSON-lines record
per run -- timestamp, video path, YouTube video ID (if uploaded), and every quote + its movie -- regardless
of whether that run uploaded successfully, so you can search back later for which video a particular quote
or movie appeared in:

```bash
grep -i "halloween" video_catalog.jsonl
```

Written atomically (write-temp + rename), same pattern as `quotes_history.txt`, so a crash mid-run can't
corrupt it.

## Automation on your Mac (launchd)

`com.billford.horrorvibes.plist` is a `launchd` user agent template. **launchd has no native "every N days"
schedule** -- it fires once a day, and `horrorvibes.orchestrator.is_run_due()` decides whether to actually do
anything, based on `automation.min_hours_between_runs` (default 36h) and the last recorded run in
`automation.state_file`. This is why `logs/last_run.json` exists.

Before installing:

1. Edit every `/Users/YOUR_USERNAME/horrorvibes` path in the plist to match where you cloned this repo and
   your actual `python3` path (`which python3` inside your venv).
2. Complete the one-time YouTube OAuth grant above, if `publish.youtube_upload: true`.

```bash
cp com.billford.horrorvibes.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.billford.horrorvibes.plist
```

On any unrecoverable failure, the run logs the error to `logs/horrorvibes.log` (rotating file handler) *and*
fires a macOS local notification (`osascript -e 'display notification ...'`), since nobody is watching an
unattended run in real time. `quotes_history.txt` and the run-cadence state file are both written via a
write-temp-then-rename pattern, so a crash mid-run can't leave either half-written.

## Archiving completed videos

Set `run.completed_video_dir` (e.g. an external drive path) to also copy each finished video there --
useful for keeping a growing library of runs somewhere other than the working `output_dir`. It's best-effort:
if the configured path lives under `/Volumes/<name>` (a macOS external-volume mount point) and that top-level
directory doesn't already exist, the drive isn't plugged in, and archiving is skipped with a logged warning
rather than failing the run -- or, worse, silently creating a same-named folder on the boot disk instead of
the actual drive, which is what a naive `mkdir(parents=True)` would do if the mount point is absent. The
video always stays in `output_dir` regardless of whether archiving succeeds.

## Directory structure

```
./
├── horrorvibes/              # the v2 package (see Architecture above)
├── run.py                    # thin CLI entrypoint
├── config.yaml                # your live config (gitignored secrets aside, safe to commit -- no secrets in it)
├── config.sample.yaml         # annotated template
├── com.billford.horrorvibes.plist
├── scripts/                   # manual smoke tests (not run by pytest)
├── tests/                     # full pytest suite, all external calls mocked
├── .env                       # OPENAI_API_KEY / ELEVENLABS_API_KEY -- never commit this
├── client_secret.json         # YouTube OAuth client secret -- never commit this
├── token.json                 # YouTube OAuth refresh token -- never commit this
├── audio/                     # curated fallback tracks for music.backend failures
├── quotes/ images/ frames/ output/   # working directories, recreated each run
└── quotes_history.txt          # dedup history, appended atomically
```

## Testing & code quality

```bash
pip install -r requirements.txt   # includes pytest, pylint, bandit
pytest -q
pylint horrorvibes run.py scripts tests
bandit -c pyproject.toml -r horrorvibes run.py scripts
```

All three are wired up in `.github/workflows/` (`tests.yml`, `pylint.yml`, `bandit.yml`) and run on every
push. Every module's non-inference, non-network logic (config parsing, prompt construction, fallback
ordering, retry/backoff, filename handling, duration math, dedup logic) has unit test coverage with every
external call (OpenAI, local SD, ElevenLabs, YouTube, `subprocess`/ffmpeg) mocked -- no network calls, no real
model inference, no real uploads happen in the test suite.

## Troubleshooting

**No quotes generated**: check `OPENAI_API_KEY` in `.env`, and `logs/horrorvibes.log` for the specific
`QuoteGenerationError`.
**Images look like gradients again**: check `logs/horrorvibes.log` for `"falling back"` warnings -- it means
local SD (and possibly the OpenAI fallback too) failed for that run; the backend actually used for each image
is logged at INFO level.
**Music sounds like the same old track**: same idea -- check for a `"fallback backend"` warning from
`musicgen`, meaning the ElevenLabs call failed and a curated file from `./audio` was used instead.
**FFmpeg errors**: make sure FFmpeg is installed and on `PATH` (`which ffmpeg`); see `INSTALL.md`.
**YouTube upload skipped**: check for a `PublishError` in the logs -- almost always a missing/expired
`token.json` needing a fresh interactive OAuth grant (see "Unattended YouTube upload" above).
**Quote repetition**: `quotes_history.txt` tracks used quotes across runs. Delete it to allow previously used
quotes again.

## Contributing

Feel free to submit issues, feature requests, or pull requests.

## License

This project is open source. Please ensure you comply with OpenAI's and ElevenLabs' usage policies and
respect copyright when using movie quotes.

## Disclaimer

This tool generates videos using movie quotes for entertainment purposes. Ensure you have proper rights for
any commercial use of the generated content.
