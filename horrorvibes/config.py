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
    },
    "quotes": {
        "model": "gpt-4",
        "max_attempts": 5,
        "themes": [
            "classic horror",
            "modern horror",
            "psychological horror",
            "slasher films",
            "supernatural horror",
            "zombie films",
            "vampire movies",
            "ghost stories",
        ],
    },
    "image": {
        "backend": "local_sd",
        "sd_model": "stabilityai/sdxl-turbo",
        "sd_device": "mps",
        "sd_steps": 4,
        "sd_guidance_scale": 0.0,
        "sd_timeout_sec": 120,
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


@dataclass(frozen=True)
class QuotesConfig:
    """Chat-model quote generation settings."""

    model: str
    max_attempts: int
    themes: list[str]


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
    logging: LoggingConfig
    automation: AutomationConfig
    raw: dict[str, Any] = field(repr=False, default_factory=dict)


def _validate(data: dict[str, Any]) -> None:
    run = data["run"]
    if int(run["quote_count"]) <= 0:
        raise ConfigError("run.quote_count must be a positive integer")
    if int(run["duration_per_quote_sec"]) <= 0:
        raise ConfigError("run.duration_per_quote_sec must be a positive integer")

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
        ),
        quotes=QuotesConfig(
            model=merged["quotes"]["model"],
            max_attempts=int(merged["quotes"]["max_attempts"]),
            themes=list(merged["quotes"]["themes"]),
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
