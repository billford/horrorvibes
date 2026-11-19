#!/usr/bin/env python3
"""Local narration worker: Chatterbox text-to-speech plus Whisper word timings, on this Mac.

Run by horrorvibes.voiceover under the Chatterbox venv (``voiceover.local_python``), never imported by the
production package: Chatterbox pins a torch version the production venv must not get.

    <local_python> scripts/tts_chatterbox.py job.json

job.json::

    {"reference": "voice.wav", "results": "results.json", "cfg_weight": 0.4,
     "items": [{"text": "...", "out": "narration_1.wav", "exaggeration": 0.5}, ...]}

results.json is a list aligned with ``items``: ``{"ok": true, "word_starts": [0.12, ...] | null}`` or
``{"ok": false, "error": "..."}``. The model loads once per job, so callers should batch.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

WHISPER = "mlx-community/whisper-large-v3-turbo"


def _word_starts(path: Path, text: str) -> list[float] | None:
    """Each word's start time from local Whisper, if its word count matches the script's."""
    try:
        import mlx_whisper  # pylint: disable=import-outside-toplevel,import-error

        result = mlx_whisper.transcribe(str(path), path_or_hf_repo=WHISPER, word_timestamps=True)
    except Exception:  # pylint: disable=broad-exception-caught
        return None
    starts = [word["start"] for seg in result.get("segments", []) for word in seg.get("words", [])]
    return starts if len(starts) == len(text.split()) else None


def main() -> int:
    """Synthesize every item in the job and write the results file."""
    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    import soundfile as sf  # pylint: disable=import-outside-toplevel,import-error
    from chatterbox.tts import ChatterboxTTS  # pylint: disable=import-outside-toplevel,import-error

    model = ChatterboxTTS.from_pretrained(device="mps")
    results = []
    for item in job["items"]:
        try:
            wav = model.generate(
                item["text"],
                audio_prompt_path=job["reference"],
                exaggeration=float(item.get("exaggeration", 0.5)),
                cfg_weight=float(job.get("cfg_weight", 0.4)),
            )
            out = Path(item["out"])
            out.parent.mkdir(parents=True, exist_ok=True)
            sf.write(out, wav.squeeze(0).cpu().numpy(), model.sr)
            results.append({"ok": True, "word_starts": _word_starts(out, item["text"])})
        except Exception as exc:  # pylint: disable=broad-exception-caught
            results.append({"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]})
    Path(job["results"]).write_text(json.dumps(results), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
