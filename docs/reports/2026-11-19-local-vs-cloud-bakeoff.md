# Local vs cloud: what would running everything on lola cost in quality?

Date: 2026-11-19. Host: lola (M1 Ultra, 64 GB). Script: `scripts/bakeoff_local.py`, run in its own venv
(`~/hv_local_venv`). The raw results are in `~/horrorvibes_bakeoff/` on lola.

## Summary

- **Writing: not viable locally yet.** Qwen3 32B (4-bit, MLX) passed **0 of 7** stories. The cloud writer
  passed 6 of 7, given the same research and the same cloud fact-check.
- **Voice: close.** Chatterbox, running locally, read the same script with every name correct. Its word error
  rate was 4.0% against ElevenLabs' 1.5%, and it took about 2.5 minutes for a whole story. Whether it
  sounds good enough is the user's call: see the two videos below.

So the realistic local claim today is: *illustrated, voiced and scored on one Mac; written and
fact-checked in the cloud.*

## Writing

Every saved story's research brief was written up by each writer and graded by the same cloud claim
check, with one rewrite, as in production. The local model ran without "thinking" mode.

| Story | Cloud writer | Local writer (Qwen3 32B) |
|---|---|---|
| Tutankhamun's Trumpets | Failed (2/16 claims, then again) | Too short twice |
| Kaikoura Lights | Passed first time | Too short twice |
| The Zebrina | Passed after one rewrite | Failed claims (4/15), then too short |
| Busby's Stoop Chair | Passed first time | Too short twice |
| Nelly Butler | Passed after one rewrite | Too short twice |
| Bettiscombe Skull | Passed first time | Failed claims (3/15) |
| The Gurdon Light | Passed first time | Failed claims (3/16) |
| **Total** | **6 of 7** | **0 of 7** |

The local model ran short: 55-65 words against a target of 80-105. A second run told it why each draft
was rejected (that fix, in `stories._write_and_check`, now also helps the cloud writer), and it still ran
short. When it did reach the target length, it failed the fact-check in the ways a careful narrator must
not:

- **It stated legends as fact**, e.g. "The reported phenomenon is a ghost" and "Bettiscombe Manor is
  literally cursed".
- **It invented details**, e.g. "One of the missing men was barely seventeen" and "official records show
  the sails were intact".
- **It blended legends**, e.g. "in the headless-worker legend, the ghost searches for lost rails".

Local generation was fast (about 1 minute per draft), so speed isn't the problem.

Worth trying later: a larger model (a 70B-class model at 4-bit fits in 64 GB), or Qwen3 with thinking
switched on. Either would need to pass at least 5 of 7 to be worth adopting.

## Voice

The Gurdon Light script was read by Chatterbox (MIT licence, zero-shot voice cloning). The voice reference
was a clip of that story's ElevenLabs narration, so the comparison is between voice engines, not voices.
Each clip was transcribed by local Whisper (large-v3-turbo, MLX) and compared with the script.

| Measure | ElevenLabs (eleven_v3) | Chatterbox (local) |
|---|---|---|
| Word error rate (lower is clearer) | 1.5% | 4.0% |
| Unusual names missed by the listen-back check | 0 | 0 |
| Speech length | 56.0 s | 49.1 s |
| Time to generate | ~10 s (API) | 157 s on lola |
| Cost | ~1,500-3,000 credits per story | $0 |
| Delivery tags such as [whispers] | Yes | No; an "exaggeration" setting instead |

Videos with the same images, music and captions, both unlisted:

- **A, ElevenLabs:** https://www.youtube.com/watch?v=0Vhv1OLWCIQ
- **B, Chatterbox:** https://www.youtube.com/watch?v=EbGUfvdJSI4

Chatterbox also adds an inaudible watermark marking its audio as AI-generated. That fits with uploads
already being labelled as synthetic media.

## Notes

- Kokoro, another local voice, was tried in July 2026 (`output/kokoro_test/` on lola) and not adopted. It
  cannot clone a voice.
- Chatterbox needs `setuptools<81` in its venv: its watermarker imports `pkg_resources`.
- Local Whisper transcription worked well and could replace the OpenAI Whisper call in the listen-back
  check at no cost in quality. It needs the MLX venv, or `mlx-whisper` added to production.
