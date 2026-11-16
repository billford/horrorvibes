"""Loads and validates config.yaml.

Replaces the CLI-flag-only configuration of the original script. Every
setting has a built-in default, so a minimal or even empty config.yaml
is valid; validation only rejects values that are present but wrong.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from horrorvibes.exceptions import ConfigError

VALID_IMAGE_BACKENDS = {"local_sd", "openai", "gradient"}
VALID_MUSIC_BACKENDS = {"local", "elevenlabs", "curated_file"}
VALID_PRIVACY_STATUSES = {"private", "unlisted", "public"}
VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

DEFAULTS: dict[str, Any] = {
    "run": {
        "quote_count": 9,
        "duration_per_quote_sec": 10,
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "quotes_dir": "./quotes",
        "images_dir": "./images",
        "frames_dir": "./frames",
        "output_dir": "./output",
        "quotes_history_path": "./quotes_history.txt",
        # Optional: also copy each completed video here (e.g. an external
        # drive). None disables archiving -- videos stay in output_dir only.
        "completed_video_dir": None,
        # Durable, append-only record of every quote that's appeared in a
        # generated video, so you can search back later for which video a
        # particular quote/movie was used in.
        "video_catalog_path": "./video_catalog.jsonl",
    },
    "quotes": {
        # Responses API model. Reasoning models reject temperature, so it is
        # only sent when set (null = omit). See docs/specs/v3-diversity.md.
        "model": "gpt-5.5",
        "reasoning_effort": "low",
        "temperature": None,
        "max_attempts": 4,
        # Second pass that checks each candidate with the web_search tool and
        # drops lines it can't confirm. A failed check keeps the candidates.
        "verify": True,
        "verify_model": None,  # null = same as model
        # Quote text similarity (0-1) at or above which a quote counts as a
        # repeat of one already used.
        "fuzzy_dup_threshold": 0.88,
        # Max recently-used titles/quotes listed in the prompt as exclusions.
        "exclusion_prompt_limit": 150,
        # Each quote gets its own (decade, subgenre, region, medium) brief so a
        # batch spreads across the genre instead of the same famous films.
        "themes": [
            "slasher", "supernatural possession", "haunted house", "ghost story",
            "folk horror", "body horror", "cosmic horror", "psychological horror",
            "creature feature", "zombie", "vampire", "werewolf", "witchcraft",
            "found footage", "giallo", "gothic horror", "sci-fi horror",
            "survival horror", "home invasion", "horror comedy", "cult horror",
            "killer doll or toy", "anthology", "monster movie",
        ],
        "decades": [1920, 1930, 1940, 1950, 1960, 1970, 1980, 1990, 2000, 2010, 2020],
        # Relative sampling weights. Weighted toward English-language titles,
        # where quote recall (and verification) is most reliable.
        "regions": {
            "American": 5, "British": 2, "Japanese": 1, "South Korean": 1,
            "Spanish": 0.5, "Italian": 0.5, "French": 0.5, "Mexican": 0.5,
            "Canadian": 0.5, "Australian": 0.5,
        },
        # Fraction of slots that ask for a TV series instead of a film.
        "tv_share": 0.3,
        # Durable record of which movie/show each quote came from, so the
        # same one isn't quoted again too soon.
        "movie_history_path": "./movie_history.jsonl",
        "movie_cooldown_days": 30,
    },
    "image": {
        "backend": "local_sd",
        # Full SDXL base, not sdxl-turbo: turbo's 1-4 step distillation trades
        # away prompt adherence for speed, and images stopped meaningfully
        # relating to their quote/movie as a result. Slower per image, but
        # cadence is every-other-day, so there's no real-time pressure.
        "sd_model": "stabilityai/stable-diffusion-xl-base-1.0",
        "sd_device": "mps",
        "sd_steps": 30,
        "sd_guidance_scale": 7.0,
        "sd_timeout_sec": 300,
        "openai_model": "gpt-image-1",
        "openai_timeout_sec": 60,
        "style_suffix": (
            "expressionist horror painting, dramatic chiaroscuro lighting, "
            "high-contrast brushwork, bruised muted palette, textured canvas"
        ),
    },
    "music": {
        "backend": "elevenlabs",
        "model_id": "music_v1",
        "mood_anchor": "meditative horror ambient, no vocals, no percussion hits",
        "mood_pool": ["slow", "sparse", "drone", "unsettling calm", "minor key", "distant"],
        "mood_pool_sample_size": 3,
        "api_timeout_sec": 120,
        "max_retries": 3,
        "retry_backoff_sec": 5,
        "fallback_dir": "./audio",
        # Curated library tracks are almost always registered with YouTube
        # Content ID, so a video using one gets auto-claimed even when you hold
        # a valid license. By default, a run that fell back to a curated track
        # keeps the video locally and skips the upload instead of publishing it.
        "allow_curated_upload": False,
        "local_model": "stabilityai/stable-audio-open-1.0",
        "local_device": "mps",
        "local_steps": 100,
        "local_segment_sec": 40,
        "local_crossfade_sec": 3,
        "local_negative_prompt": "vocals, singing, lyrics, percussion, drums, low quality",
    },
    "compositor": {
        "font_paths": [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/Library/Fonts/Arial Bold.ttf",
            "/Library/Fonts/Helvetica.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ],
        "quote_font_size": 60,
        "movie_font_size": 48,
        "max_chars_per_line": 25,
        "scrim_opacity": 150,
    },
    "publish": {
        "youtube_upload": True,
        "privacy_status": "private",
        "category_id": "17",
        "client_secrets_path": "./client_secret.json",
        "token_path": "./token.json",  # nosec B105 - a file path default, not a credential value
    },
    "voiceover": {
        "enabled": False,
        "voice_id": "2EiwWnXFnvU5JabPnv8n",  # "Clyde" -- gruff, unsettling; audition and swap freely
        "model_id": "eleven_multilingual_v2",
        "stability": 0.5,
        "similarity_boost": 0.75,
        "style": 0.3,
        "use_speaker_boost": True,
        "speed": 0.75,  # slower, more menacing delivery -- 0.7 is ElevenLabs' floor before quality degrades
        "api_timeout_sec": 60,
        "max_retries": 3,
        "retry_backoff_sec": 5,
        # Music volume during each narration's exact window (linear, 0-1) and
        # narration's own gain boost -- see voiceover.py for why this replaced
        # a sidechaincompress-based approach.
        "duck_volume": 0.3,
        "narration_gain": 1.6,
        # Extra seconds a quote stays on screen past its narration's own
        # length, so the next quote's narration never starts before this
        # one has actually finished.
        "narration_pad_sec": 1.0,
    },
    "logging": {
        "level": "INFO",
        "file": "./logs/horrorvibes.log",
        "max_bytes": 5_000_000,
        "backup_count": 3,
    },
    "automation": {
        "min_hours_between_runs": 36,
        "state_file": "./logs/last_run.json",
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


@dataclass(frozen=True)
class RunConfig:
    """Per-run sizing and directory layout."""

    quote_count: int
    duration_per_quote_sec: int
    width: int
    height: int
    fps: int
    quotes_dir: Path
    images_dir: Path
    frames_dir: Path
    output_dir: Path
    quotes_history_path: Path
    completed_video_dir: Path | None
    video_catalog_path: Path


@dataclass(frozen=True)
class QuotesConfig:
    """Chat-model quote generation settings."""

    model: str
    max_attempts: int
    temperature: float | None
    themes: list[str]
    movie_history_path: Path
    movie_cooldown_days: float
    reasoning_effort: str | None = "low"
    verify: bool = True
    verify_model: str | None = None
    fuzzy_dup_threshold: float = 0.88
    exclusion_prompt_limit: int = 150
    decades: list[int] = field(default_factory=lambda: list(range(1920, 2030, 10)))
    regions: dict[str, float] = field(default_factory=lambda: {"American": 1.0})
    tv_share: float = 0.3


@dataclass(frozen=True)
class ImageConfig:
    """Image backend chain settings (local SD -> OpenAI -> gradient)."""

    backend: str
    sd_model: str
    sd_device: str
    sd_steps: int
    sd_guidance_scale: float
    sd_timeout_sec: int
    openai_model: str
    openai_timeout_sec: int
    style_suffix: str


@dataclass(frozen=True)
class MusicConfig:
    """Music backend chain settings (local Stable Audio -> ElevenLabs -> curated file)."""

    backend: str
    model_id: str
    mood_anchor: str
    mood_pool: list[str]
    mood_pool_sample_size: int
    api_timeout_sec: int
    max_retries: int
    retry_backoff_sec: int
    fallback_dir: Path
    allow_curated_upload: bool
    local_model: str
    local_device: str
    local_steps: int
    local_segment_sec: float
    local_crossfade_sec: float
    local_negative_prompt: str


@dataclass(frozen=True)
class CompositorConfig:
    """Text-on-image rendering settings."""

    font_paths: list[str]
    quote_font_size: int
    movie_font_size: int
    max_chars_per_line: int
    scrim_opacity: int


@dataclass(frozen=True)
class PublishConfig:
    """YouTube upload settings."""

    youtube_upload: bool
    privacy_status: str
    category_id: str
    client_secrets_path: Path
    token_path: Path


@dataclass(frozen=True)
class VoiceoverConfig:
    """ElevenLabs text-to-speech narration + music-ducking settings."""

    enabled: bool
    voice_id: str
    model_id: str
    stability: float
    similarity_boost: float
    style: float
    use_speaker_boost: bool
    speed: float
    api_timeout_sec: int
    max_retries: int
    retry_backoff_sec: int
    duck_volume: float
    narration_gain: float
    narration_pad_sec: float


@dataclass(frozen=True)
class LoggingConfig:
    """Console + rotating-file logging settings."""

    level: str
    file: Path
    max_bytes: int
    backup_count: int


@dataclass(frozen=True)
class AutomationConfig:
    """launchd cadence-guard settings."""

    min_hours_between_runs: int
    state_file: Path


@dataclass(frozen=True)
class Config:
    """The fully merged, validated configuration for one run."""

    run: RunConfig
    quotes: QuotesConfig
    image: ImageConfig
    music: MusicConfig
    compositor: CompositorConfig
    publish: PublishConfig
    voiceover: VoiceoverConfig
    logging: LoggingConfig
    automation: AutomationConfig
    raw: dict[str, Any] = field(repr=False, default_factory=dict)


def _validate_quotes(quotes_cfg: dict[str, Any]) -> None:
    if not quotes_cfg["themes"]:
        raise ConfigError("quotes.themes must list at least one subgenre")
    if not quotes_cfg["decades"]:
        raise ConfigError("quotes.decades must list at least one decade")
    if not isinstance(quotes_cfg["regions"], dict) or not quotes_cfg["regions"]:
        raise ConfigError("quotes.regions must be a non-empty mapping of region -> weight")
    if any(float(w) < 0 for w in quotes_cfg["regions"].values()) or not any(
        float(w) > 0 for w in quotes_cfg["regions"].values()
    ):
        raise ConfigError("quotes.regions weights must be >= 0 with at least one > 0")
    if not 0.0 <= float(quotes_cfg["tv_share"]) <= 1.0:
        raise ConfigError("quotes.tv_share must be between 0 and 1")
    if not 0.0 < float(quotes_cfg["fuzzy_dup_threshold"]) <= 1.0:
        raise ConfigError("quotes.fuzzy_dup_threshold must be in (0, 1]")


def _validate(data: dict[str, Any]) -> None:
    run = data["run"]
    if int(run["quote_count"]) <= 0:
        raise ConfigError("run.quote_count must be a positive integer")
    if int(run["duration_per_quote_sec"]) <= 0:
        raise ConfigError("run.duration_per_quote_sec must be a positive integer")

    _validate_quotes(data["quotes"])

    image_backend = data["image"]["backend"]
    if image_backend not in VALID_IMAGE_BACKENDS:
        raise ConfigError(
            f"image.backend must be one of {sorted(VALID_IMAGE_BACKENDS)}, got {image_backend!r}"
        )

    music_backend = data["music"]["backend"]
    if music_backend not in VALID_MUSIC_BACKENDS:
        raise ConfigError(
            f"music.backend must be one of {sorted(VALID_MUSIC_BACKENDS)}, got {music_backend!r}"
        )
    if int(data["music"]["mood_pool_sample_size"]) > len(data["music"]["mood_pool"]):
        raise ConfigError("music.mood_pool_sample_size cannot exceed len(music.mood_pool)")
    if float(data["music"]["local_crossfade_sec"]) >= float(data["music"]["local_segment_sec"]):
        raise ConfigError("music.local_crossfade_sec must be less than music.local_segment_sec")

    privacy_status = data["publish"]["privacy_status"]
    if privacy_status not in VALID_PRIVACY_STATUSES:
        raise ConfigError(
            f"publish.privacy_status must be one of {sorted(VALID_PRIVACY_STATUSES)}, "
            f"got {privacy_status!r}"
        )

    log_level = data["logging"]["level"].upper()
    if log_level not in VALID_LOG_LEVELS:
        raise ConfigError(f"logging.level must be one of {sorted(VALID_LOG_LEVELS)}, got {log_level!r}")


def load_config(path: str | Path) -> Config:
    """Load config.yaml from ``path``, merge with defaults, and validate.

    A missing file is not an error: defaults alone produce a valid Config,
    matching the "minimal config.yaml is valid" contract above.
    """
    path = Path(path)
    user_data: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"Failed to parse {path}: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ConfigError(f"{path} must contain a YAML mapping at the top level")
        user_data = loaded

    merged = _deep_merge(DEFAULTS, user_data)
    _validate(merged)

    return Config(
        run=RunConfig(
            quote_count=int(merged["run"]["quote_count"]),
            duration_per_quote_sec=int(merged["run"]["duration_per_quote_sec"]),
            width=int(merged["run"]["width"]),
            height=int(merged["run"]["height"]),
            fps=int(merged["run"]["fps"]),
            quotes_dir=Path(merged["run"]["quotes_dir"]),
            images_dir=Path(merged["run"]["images_dir"]),
            frames_dir=Path(merged["run"]["frames_dir"]),
            output_dir=Path(merged["run"]["output_dir"]),
            quotes_history_path=Path(merged["run"]["quotes_history_path"]),
            completed_video_dir=(
                Path(merged["run"]["completed_video_dir"])
                if merged["run"]["completed_video_dir"]
                else None
            ),
            video_catalog_path=Path(merged["run"]["video_catalog_path"]),
        ),
        quotes=QuotesConfig(
            model=merged["quotes"]["model"],
            max_attempts=int(merged["quotes"]["max_attempts"]),
            temperature=(
                float(merged["quotes"]["temperature"])
                if merged["quotes"]["temperature"] is not None
                else None
            ),
            themes=list(merged["quotes"]["themes"]),
            movie_history_path=Path(merged["quotes"]["movie_history_path"]),
            movie_cooldown_days=float(merged["quotes"]["movie_cooldown_days"]),
            reasoning_effort=merged["quotes"]["reasoning_effort"] or None,
            verify=bool(merged["quotes"]["verify"]),
            verify_model=merged["quotes"]["verify_model"] or None,
            fuzzy_dup_threshold=float(merged["quotes"]["fuzzy_dup_threshold"]),
            exclusion_prompt_limit=int(merged["quotes"]["exclusion_prompt_limit"]),
            decades=[int(d) for d in merged["quotes"]["decades"]],
            regions={str(k): float(v) for k, v in merged["quotes"]["regions"].items()},
            tv_share=float(merged["quotes"]["tv_share"]),
        ),
        image=ImageConfig(
            backend=merged["image"]["backend"],
            sd_model=merged["image"]["sd_model"],
            sd_device=merged["image"]["sd_device"],
            sd_steps=int(merged["image"]["sd_steps"]),
            sd_guidance_scale=float(merged["image"]["sd_guidance_scale"]),
            sd_timeout_sec=int(merged["image"]["sd_timeout_sec"]),
            openai_model=merged["image"]["openai_model"],
            openai_timeout_sec=int(merged["image"]["openai_timeout_sec"]),
            style_suffix=merged["image"]["style_suffix"],
        ),
        music=MusicConfig(
            backend=merged["music"]["backend"],
            model_id=merged["music"]["model_id"],
            mood_anchor=merged["music"]["mood_anchor"],
            mood_pool=list(merged["music"]["mood_pool"]),
            mood_pool_sample_size=int(merged["music"]["mood_pool_sample_size"]),
            api_timeout_sec=int(merged["music"]["api_timeout_sec"]),
            max_retries=int(merged["music"]["max_retries"]),
            retry_backoff_sec=int(merged["music"]["retry_backoff_sec"]),
            fallback_dir=Path(merged["music"]["fallback_dir"]),
            allow_curated_upload=bool(merged["music"]["allow_curated_upload"]),
            local_model=merged["music"]["local_model"],
            local_device=merged["music"]["local_device"],
            local_steps=int(merged["music"]["local_steps"]),
            local_segment_sec=float(merged["music"]["local_segment_sec"]),
            local_crossfade_sec=float(merged["music"]["local_crossfade_sec"]),
            local_negative_prompt=merged["music"]["local_negative_prompt"],
        ),
        compositor=CompositorConfig(
            font_paths=list(merged["compositor"]["font_paths"]),
            quote_font_size=int(merged["compositor"]["quote_font_size"]),
            movie_font_size=int(merged["compositor"]["movie_font_size"]),
            max_chars_per_line=int(merged["compositor"]["max_chars_per_line"]),
            scrim_opacity=int(merged["compositor"]["scrim_opacity"]),
        ),
        publish=PublishConfig(
            youtube_upload=bool(merged["publish"]["youtube_upload"]),
            privacy_status=merged["publish"]["privacy_status"],
            category_id=str(merged["publish"]["category_id"]),
            client_secrets_path=Path(merged["publish"]["client_secrets_path"]),
            token_path=Path(merged["publish"]["token_path"]),
        ),
        voiceover=VoiceoverConfig(
            enabled=bool(merged["voiceover"]["enabled"]),
            voice_id=merged["voiceover"]["voice_id"],
            model_id=merged["voiceover"]["model_id"],
            stability=float(merged["voiceover"]["stability"]),
            similarity_boost=float(merged["voiceover"]["similarity_boost"]),
            style=float(merged["voiceover"]["style"]),
            use_speaker_boost=bool(merged["voiceover"]["use_speaker_boost"]),
            speed=float(merged["voiceover"]["speed"]),
            api_timeout_sec=int(merged["voiceover"]["api_timeout_sec"]),
            max_retries=int(merged["voiceover"]["max_retries"]),
            retry_backoff_sec=int(merged["voiceover"]["retry_backoff_sec"]),
            duck_volume=float(merged["voiceover"]["duck_volume"]),
            narration_gain=float(merged["voiceover"]["narration_gain"]),
            narration_pad_sec=float(merged["voiceover"]["narration_pad_sec"]),
        ),
        logging=LoggingConfig(
            level=merged["logging"]["level"].upper(),
            file=Path(merged["logging"]["file"]),
            max_bytes=int(merged["logging"]["max_bytes"]),
            backup_count=int(merged["logging"]["backup_count"]),
        ),
        automation=AutomationConfig(
            min_hours_between_runs=int(merged["automation"]["min_hours_between_runs"]),
            state_file=Path(merged["automation"]["state_file"]),
        ),
        raw=merged,
    )
