#!/usr/bin/env python3
"""How much quality does running locally cost? Compare cloud and local writing and narration.

Runs in its own venv (chatterbox-tts pins torch versions the production venv must not get):

    ~/hv_local_venv/bin/python scripts/bakeoff_local.py writing   # local vs cloud script writer
    ~/hv_local_venv/bin/python scripts/bakeoff_local.py voice     # ElevenLabs vs Chatterbox + videos

**writing**: for every saved story under ``stories/``, the same research brief is written up by the cloud
model and by a local MLX model, and both go through the *same* cloud claim check with one rewrite, exactly
as in production. Measures: shape problems, claims failed on the first check, and whether it passed.

**voice**: the Gurdon Light test story's script is narrated by Chatterbox, using that story's own
ElevenLabs clip as the voice reference, so the comparison is voice engine, not voice choice. Each clip is
transcribed with local Whisper; we compare word error rate and listen-back name checks against the
ElevenLabs clips, then render two unlisted review videos with the same images and music:
B = cloud script + Chatterbox, C = local script + Chatterbox (+ local Whisper word timings).

Results go to ~/horrorvibes_bakeoff/. Nothing here touches production state.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import random
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The local-model packages live only in the bake-off venv, not the production one CI lints with.
# pylint: disable=wrong-import-position,import-error,protected-access,too-many-locals
from dotenv import load_dotenv
from openai import OpenAI

from horrorvibes import compositor, episode, listenback, orchestrator, publish, stories, voiceover
from horrorvibes.config import load_config

# pylint: enable=wrong-import-position

OUT = Path.home() / "horrorvibes_bakeoff"
GURDON = OUT / "gurdon"
LOCAL_LLM = "mlx-community/Qwen3-32B-4bit"
LOCAL_WHISPER = "mlx-community/whisper-large-v3-turbo"


# ---- a client that sends script writing to a local model and everything else to OpenAI ----------------


class _LocalWriter:  # pylint: disable=too-few-public-methods
    """mlx-lm behind the bit of the Responses API surface stories.py uses."""

    def __init__(self, repo: str):
        from mlx_lm import load  # pylint: disable=import-outside-toplevel

        self.model, self.tokenizer = load(repo)
        self.seconds = 0.0

    def __call__(self, kwargs: dict) -> str:
        from mlx_lm import generate  # pylint: disable=import-outside-toplevel
        from mlx_lm.sample_utils import make_sampler  # pylint: disable=import-outside-toplevel

        schema = kwargs["text"]["format"]["schema"]
        system = kwargs["instructions"] + (
            "\n\nReply with ONLY one JSON object, no prose and no code fences, matching this JSON schema:\n"
            + json.dumps(schema)
        )
        messages = [{"role": "system", "content": system}, {"role": "user", "content": kwargs["input"]}]
        prompt = self.tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=False, enable_thinking=False
        )
        start = time.time()
        text = generate(
            self.model, self.tokenizer, prompt=prompt, max_tokens=2500, sampler=make_sampler(temp=0.7)
        )
        self.seconds += time.time() - start
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
        match = re.search(r"\{.*\}", text, flags=re.S)
        return match.group(0) if match else "{}"


class RouterClient:  # pylint: disable=too-few-public-methods
    """stories.py calls client.responses.create(**kwargs); route by the request's schema name."""

    def __init__(self, cloud: OpenAI, local: _LocalWriter | None, log: list):
        self._cloud, self._local, self._log = cloud, local, log
        self.responses = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        name = kwargs["text"]["format"]["name"]
        if name == "story_script" and self._local is not None:
            output = self._local(kwargs)
        else:
            output = self._cloud.responses.create(**kwargs).output_text
        if name == "story_claims":
            claims = json.loads(output or "{}").get("claims") or []
            self._log.append({"claims": len(claims), "failed": stories.failed_claims(claims)})
        if name == "story_script":
            self._log.append({"script": output})
        return SimpleNamespace(output_text=output)


def _load_audit(path: Path) -> tuple[stories.Topic, stories.Research, dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return stories.Topic(**data["topic"]), stories.Research(**data["research"]), data


def _story_from_audit(data: dict) -> stories.Story:
    script = data["script"]
    beats = [stories.Beat(**beat) for beat in script["beats"]]
    return stories.Story(
        topic=stories.Topic(**data["topic"]),
        research=stories.Research(**data["research"]),
        script=stories.Script(title=script["title"], beats=beats),
        claims=data.get("claims", []),
    )


# ---- writing ---------------------------------------------------------------------------------------------


def run_writing(cfg, audits: list[Path], writers: tuple[str, ...] = ("cloud", "local")) -> None:
    """Every saved research brief, written by cloud and by local, checked by the same cloud checker."""
    cloud = OpenAI()
    local = _LocalWriter(LOCAL_LLM)
    results = []
    for path in audits:
        topic, research, _ = _load_audit(path)
        for writer in writers:
            log: list = []
            client = RouterClient(cloud, local if writer == "local" else None, log)
            started = time.time()
            outcome = stories._write_and_check(client, cfg, topic, research)
            checks = [entry for entry in log if "claims" in entry]
            scripts = [entry["script"] for entry in log if "script" in entry]
            result = {
                "topic": topic.name,
                "writer": writer,
                "passed": outcome is not None,
                "drafts": len(scripts),
                "first_check_failed": len(checks[0]["failed"]) if checks else None,
                "first_check_claims": checks[0]["claims"] if checks else None,
                "failed_examples": [c["claim"] for check in checks for c in check["failed"]][:4],
                "seconds": round(time.time() - started),
                "script": dataclasses.asdict(outcome[0]) if outcome else None,
                "drafts_raw": scripts,
            }
            print(json.dumps({k: v for k, v in result.items() if k not in ("script", "drafts_raw")}))
            results.append(result)
    OUT.mkdir(exist_ok=True)
    out = OUT / ("writing.json" if len(writers) > 1 else f"writing_{writers[0]}.json")
    out.write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(f"local model generation time: {local.seconds:.0f} s")


# ---- voice -----------------------------------------------------------------------------------------------


def _wer(reference: str, hypothesis: str) -> float:
    ref, hyp = listenback.fold(reference).split(), listenback.fold(hypothesis).split()
    row = list(range(len(hyp) + 1))
    for i, ref_word in enumerate(ref, 1):
        prev, row[0] = row[0], i
        for j, hyp_word in enumerate(hyp, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (ref_word != hyp_word))
    return row[-1] / max(1, len(ref))


def _whisper(path: Path) -> dict:
    import mlx_whisper  # pylint: disable=import-outside-toplevel

    return mlx_whisper.transcribe(str(path), path_or_hf_repo=LOCAL_WHISPER, word_timestamps=True)


def _word_starts(result: dict, text: str) -> list[float] | None:
    starts = [word["start"] for segment in result.get("segments", []) for word in segment.get("words", [])]
    return starts if len(starts) == len(text.split()) else None


def _chatterbox_clips(story: stories.Story, reference: Path, out_dir: Path) -> list[Path]:
    import soundfile as sf  # pylint: disable=import-outside-toplevel
    from chatterbox.tts import ChatterboxTTS  # pylint: disable=import-outside-toplevel

    model = ChatterboxTTS.from_pretrained(device="mps")
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, beat in enumerate(story.script.beats, 1):
        whisper = beat.delivery in ("whispers", "calm", "sad", "tired")
        wav = model.generate(
            beat.narration,
            audio_prompt_path=str(reference),
            exaggeration=0.35 if whisper else 0.6,
            cfg_weight=0.4,
        )
        path = out_dir / f"beat_{i}.wav"
        sf.write(path, wav.squeeze(0).cpu().numpy(), model.sr)
        paths.append(path)
    return paths


def _score(story: stories.Story, clips: list[Path]) -> dict:
    wers, missing, narrations = [], [], []
    for beat, clip in zip(story.script.beats, clips):
        result = _whisper(clip)
        wers.append(_wer(beat.narration, result["text"]))
        missing += [
            m["name"]
            for m in listenback.missing_names(result["text"], beat.narration, story.research.names)
        ]
        narrations.append(voiceover.Narration(path=clip, word_starts=_word_starts(result, beat.narration)))
    seconds = sum(voiceover.narration_duration_sec(clip) for clip in clips)
    return {
        "wer": round(sum(wers) / len(wers), 3),
        "names_missed": missing,
        "speech_sec": round(seconds, 1),
        "narrations": narrations,
    }


def _render(config, story: stories.Story, narrations, images: list[Path] | None, label: str) -> Path:
    """Same pack and music as the published Gurdon video; images reused when the beats are the same."""
    pack = next(p for p in config.style_packs if p.name == "expressionist_oil")
    plan = episode.EpisodePlan(pack, "story", "", 0, config.quotes)
    durations, offsets, lengths = orchestrator._story_schedule(config, narrations)
    if images:
        captioned = dataclasses.replace(pack, scrim_opacity=max(pack.scrim_opacity, 150))
        media = []
        for i, (beat, image) in enumerate(zip(story.script.beats, images)):
            layers = compositor.render_text_layers(
                beat.narration,
                story.topic.name,
                captioned,
                config.compositor,
                config.run.width,
                config.run.height,
                config.run.frames_dir / "layers",
                i,
            )
            frame = config.run.frames_dir / f"frame_{i + 1}.png"
            compositor.flatten_frame(image, layers, config.run.width, config.run.height, frame)
            media.append(orchestrator._QuoteMedia(background=image, layers=layers, frame=frame))
    else:
        media = orchestrator._render_beat_media(config, plan, story)
    audio = orchestrator._build_final_audio_track(
        config, GURDON / "_music.mp3", narrations, lengths, offsets
    )
    out = OUT / f"gurdon_{label}.mp4"
    return orchestrator._assemble(
        config, plan, media, narrations, lengths, durations, audio, out, random.Random(7)
    )


def run_voice(config, upload: bool) -> None:
    """Narrate the Gurdon script with Chatterbox, score both voices, render and upload review videos."""
    _, _, data = _load_audit(GURDON / "20261119_100324_gurdon_light.json")
    story = _story_from_audit(data)
    eleven = [
        GURDON / "narration" / f"beat_{i}" / "narration_1.mp3"
        for i in range(1, len(story.script.beats) + 1)
    ]
    scores = {"elevenlabs": _score(story, eleven)}

    started = time.time()
    chatter = _chatterbox_clips(story, eleven[1], OUT / "chatterbox_cloud_script")
    scores["chatterbox"] = _score(story, chatter)
    scores["chatterbox"]["generate_sec"] = round(time.time() - started)

    images = sorted((GURDON / "images").glob("background_*.png"))
    videos = {
        "B_cloud_script_chatterbox": _render(config, story, scores["chatterbox"]["narrations"], images, "B")
    }

    writing = (
        json.loads((OUT / "writing.json").read_text(encoding="utf-8"))
        if (OUT / "writing.json").exists()
        else []
    )
    local = next(
        (r for r in writing if r["writer"] == "local" and r["passed"] and "Gurdon" in r["topic"]), None
    )
    if local:
        local_story = dataclasses.replace(
            story,
            script=stories.Script(
                local["script"]["title"], [stories.Beat(**b) for b in local["script"]["beats"]]
            ),
        )
        clips = _chatterbox_clips(local_story, eleven[1], OUT / "chatterbox_local_script")
        scores["chatterbox_local_script"] = _score(local_story, clips)
        videos["C_local_script_chatterbox"] = _render(
            config, local_story, scores["chatterbox_local_script"]["narrations"], None, "C"
        )

    summary = {name: {k: v for k, v in s.items() if k != "narrations"} for name, s in scores.items()}
    links = {}
    if upload:
        unlisted = dataclasses.replace(config.publish, privacy_status="unlisted")
        for name, path in videos.items():
            title = f"[Local test {name[0]}] {story.script.title}"[:100]
            links[name] = publish.publish(
                path, title, story.description(), stories.story_tags(story), unlisted
            )
    (OUT / "voice.json").write_text(
        json.dumps(
            {"scores": summary, "videos": {k: str(v) for k, v in videos.items()}, "youtube": links},
            indent=1,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"scores": summary, "youtube": links}, indent=1))


def main() -> int:
    """Parse the mode and run it from the repo root."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("mode", choices=["writing", "voice"])
    parser.add_argument("--no-upload", action="store_true", help="voice mode: render but don't upload")
    parser.add_argument("--writer", choices=["cloud", "local"], help="writing mode: run only this writer")
    args = parser.parse_args()
    load_dotenv(".env")
    config = load_config("config.yaml")
    scratch = OUT / "work"
    config = dataclasses.replace(
        config,
        run=dataclasses.replace(
            config.run,
            output_dir=scratch / "output",
            images_dir=scratch / "images",
            frames_dir=scratch / "frames",
        ),
    )
    for directory in (config.run.output_dir, config.run.images_dir, config.run.frames_dir):
        directory.mkdir(parents=True, exist_ok=True)
    if args.mode == "writing":
        writers = (args.writer,) if args.writer else ("cloud", "local")
        run_writing(config.stories, sorted(Path("stories").glob("*.json")), writers)
    else:
        run_voice(config, upload=not args.no_upload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
