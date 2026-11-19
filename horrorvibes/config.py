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

from horrorvibes.defaults import DEFAULTS
from horrorvibes.exceptions import ConfigError

VALID_IMAGE_BACKENDS = {"mflux", "local_sd", "openai", "gradient"}
VALID_LAYOUTS = {"upper", "center", "lower"}
VALID_OVERLAYS = {"none", "grain", "vhs"}
VALID_CAPTION_MODES = {"karaoke", "static"}
# Subset of ffmpeg xfade transitions that read well on a portrait short.
VALID_TRANSITIONS = {
    "fade", "fadeblack", "fadewhite", "dissolve", "pixelize", "hblur", "radial",
    "circleopen", "circleclose", "wipeleft", "wiperight", "wipeup", "wipedown",
    "slideleft", "slideright", "slideup", "slidedown", "smoothup", "smoothdown",
    "fadegrays", "zoomin", "squeezev",
}
VALID_MUSIC_BACKENDS = {"local", "elevenlabs", "curated_file"}
VALID_PRIVACY_STATUSES = {"private", "unlisted", "public"}
VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


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
    mflux_command: str = "mflux-generate-flux2"
    mflux_model: str = "flux2-klein-4b"
    mflux_steps: int = 4
    mflux_width: int = 768
    mflux_height: int = 1344
    mflux_timeout_sec: int = 900


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
    contains_synthetic_media: bool = True
    source_url: str | None = None


@dataclass(frozen=True)
class VoiceoverConfig:  # pylint: disable=too-many-instance-attributes
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
    fallback_model_id: str | None = None
    narration_lufs: float = -16.0
    music_lufs: float = -27.0
    use_delivery_tags: bool = True
    use_timestamps: bool = True


@dataclass(frozen=True)
class StylePack:  # pylint: disable=too-many-instance-attributes
    """One run's visual and sonic identity."""

    name: str
    image_style: str
    font_paths: list[str] = field(default_factory=list)
    layout: str = "center"
    text_color: tuple[int, int, int] = (255, 255, 255)
    highlight_color: tuple[int, int, int] = (255, 255, 255)
    scrim_opacity: int = 140
    stroke_width: int = 0
    grade: str = ""
    overlay: str = "grain"
    vignette: bool = True
    transitions: list[str] = field(default_factory=lambda: ["fade"])
    music_mood_anchor: str | None = None
    music_mood_pool: list[str] | None = None
    voice_id: str | None = None


@dataclass(frozen=True)
class EpisodeFormat:
    """A themed episode: narrows the quote briefs and names the video."""

    name: str
    headline: str
    instruction: str | None = None
    themes: list[str] | None = None
    decades: list[int] | None = None
    regions: dict[str, float] | None = None
    tv_share: float | None = None


@dataclass(frozen=True)
class EpisodesConfig:
    """How each run picks its format and length."""

    enabled: bool
    history_path: Path
    themed_share: float
    quote_count_min: int
    quote_count_max: int
    mixed_headlines: list[str]
    formats: list[EpisodeFormat]


@dataclass(frozen=True)
class MotionConfig:
    """Animated assembly settings (see video.py)."""

    enabled: bool
    transition_sec: float
    zoom_amount: float
    upscale_factor: float
    caption_mode: str
    drift: float
    ripple_px: float
    flicker: float
    crf: int
    preset: str


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
class StoriesConfig:  # pylint: disable=too-many-instance-attributes
    """Story Shorts: topic choice, research, writing and claim checking."""

    enabled: bool
    model: str
    reasoning_effort: str | None
    history_path: Path
    audit_dir: Path
    channel_snapshot_path: Path
    categories: dict[str, float]
    topic_candidates: int
    max_topic_attempts: int
    min_sources: int
    words_min: int
    words_max: int
    beats_min: int
    beats_max: int
    beat_words_max: int = 32
    days: tuple[str, ...] = ("mon", "thu", "fri", "sat", "sun")
    beat_pad_sec: float = 0.5
    voice_id: str | None = None


_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _build_stories(data: dict[str, Any], quotes_model: str) -> StoriesConfig:
    categories = {str(k): float(v) for k, v in (data["categories"] or {}).items()}
    if not categories or any(w < 0 for w in categories.values()) or not any(categories.values()):
        raise ConfigError("stories.categories must map category -> weight, with at least one weight > 0")
    if not 1 <= int(data["words_min"]) <= int(data["words_max"]):
        raise ConfigError("stories.words_min must be >= 1 and <= words_max")
    if not 1 <= int(data["beats_min"]) <= int(data["beats_max"]):
        raise ConfigError("stories.beats_min must be >= 1 and <= beats_max")
    days = tuple(str(day).lower()[:3] for day in data["days"] or [])
    if any(day not in _WEEKDAYS for day in days):
        raise ConfigError(f"stories.days must be weekday names, e.g. {list(_WEEKDAYS)}")
    for key in ("topic_candidates", "max_topic_attempts", "min_sources", "beat_words_max"):
        if int(data[key]) < 1:
            raise ConfigError(f"stories.{key} must be at least 1")
    return StoriesConfig(
        enabled=bool(data["enabled"]),
        model=str(data["model"] or quotes_model),
        reasoning_effort=data["reasoning_effort"] or None,
        history_path=Path(data["history_path"]),
        audit_dir=Path(data["audit_dir"]),
        channel_snapshot_path=Path(data["channel_snapshot_path"]),
        categories=categories,
        topic_candidates=int(data["topic_candidates"]),
        max_topic_attempts=int(data["max_topic_attempts"]),
        min_sources=int(data["min_sources"]),
        words_min=int(data["words_min"]),
        words_max=int(data["words_max"]),
        beats_min=int(data["beats_min"]),
        beats_max=int(data["beats_max"]),
        beat_words_max=int(data["beat_words_max"]),
        days=days,
        beat_pad_sec=float(data["beat_pad_sec"]),
        voice_id=data["voice_id"] or None,
    )


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
    motion: MotionConfig | None = None
    episodes: EpisodesConfig | None = None
    stories: StoriesConfig | None = None
    style_packs: list[StylePack] = field(default_factory=list)
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


def _validate_style_packs(packs: Any) -> None:
    if not isinstance(packs, list) or not packs:
        raise ConfigError("style_packs must be a non-empty list")
    names = [pack.get("name") for pack in packs]
    if any(not name for name in names) or len(set(names)) != len(names):
        raise ConfigError("every style pack needs a unique name")
    for pack in packs:
        if not pack.get("image_style"):
            raise ConfigError(f"style pack {pack['name']!r} needs an image_style")
        if pack.get("layout", "center") not in VALID_LAYOUTS:
            raise ConfigError(f"style pack {pack['name']!r}: layout must be one of {sorted(VALID_LAYOUTS)}")
        if pack.get("overlay", "grain") not in VALID_OVERLAYS:
            raise ConfigError(f"style pack {pack['name']!r}: overlay must be one of {sorted(VALID_OVERLAYS)}")
        bad = [t for t in pack.get("transitions", ["fade"]) if t not in VALID_TRANSITIONS]
        if bad or not pack.get("transitions", ["fade"]):
            raise ConfigError(f"style pack {pack['name']!r}: unsupported transitions {bad}")


def _validate_motion_and_episodes(motion: dict[str, Any], episodes: dict[str, Any]) -> None:
    if motion["caption_mode"] not in VALID_CAPTION_MODES:
        raise ConfigError(f"motion.caption_mode must be one of {sorted(VALID_CAPTION_MODES)}")
    if not 0 <= float(motion["transition_sec"]) < 3:
        raise ConfigError("motion.transition_sec must be in [0, 3)")
    if not 0 <= float(motion["zoom_amount"]) <= 0.5:
        raise ConfigError("motion.zoom_amount must be in [0, 0.5]")
    if not 0 <= float(motion["drift"]) <= 1:
        raise ConfigError("motion.drift must be in [0, 1]")
    if not 0 <= float(motion["ripple_px"]) <= 20:
        raise ConfigError("motion.ripple_px must be in [0, 20]")
    if not 0 <= float(motion["flicker"]) <= 0.2:
        raise ConfigError("motion.flicker must be in [0, 0.2]")
    if not 1 <= int(episodes["quote_count_min"]) <= int(episodes["quote_count_max"]):
        raise ConfigError("episodes.quote_count_min must be >= 1 and <= quote_count_max")
    if not 0.0 <= float(episodes["themed_share"]) <= 1.0:
        raise ConfigError("episodes.themed_share must be between 0 and 1")
    if not episodes["mixed_headlines"]:
        raise ConfigError("episodes.mixed_headlines must not be empty")
    for fmt in episodes["formats"]:
        if not fmt.get("name") or not fmt.get("headline"):
            raise ConfigError("every episode format needs a name and a headline")


def _validate(data: dict[str, Any]) -> None:
    run = data["run"]
    if int(run["quote_count"]) <= 0:
        raise ConfigError("run.quote_count must be a positive integer")
    if int(run["duration_per_quote_sec"]) <= 0:
        raise ConfigError("run.duration_per_quote_sec must be a positive integer")

    _validate_quotes(data["quotes"])
    _validate_style_packs(data["style_packs"])
    _validate_motion_and_episodes(data["motion"], data["episodes"])

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


def _build_motion(data: dict[str, Any]) -> MotionConfig:
    return MotionConfig(
        enabled=bool(data["enabled"]),
        transition_sec=float(data["transition_sec"]),
        zoom_amount=float(data["zoom_amount"]),
        upscale_factor=float(data["upscale_factor"]),
        caption_mode=str(data["caption_mode"]),
        drift=float(data["drift"]),
        ripple_px=float(data["ripple_px"]),
        flicker=float(data["flicker"]),
        crf=int(data["crf"]),
        preset=str(data["preset"]),
    )


def _build_episodes(data: dict[str, Any]) -> EpisodesConfig:
    formats = [
        EpisodeFormat(
            name=str(fmt["name"]),
            headline=str(fmt["headline"]),
            instruction=fmt.get("instruction"),
            themes=list(fmt["themes"]) if fmt.get("themes") else None,
            decades=[int(d) for d in fmt["decades"]] if fmt.get("decades") else None,
            regions={str(k): float(v) for k, v in fmt["regions"].items()} if fmt.get("regions") else None,
            tv_share=float(fmt["tv_share"]) if fmt.get("tv_share") is not None else None,
        )
        for fmt in data["formats"]
    ]
    return EpisodesConfig(
        enabled=bool(data["enabled"]),
        history_path=Path(data["history_path"]),
        themed_share=float(data["themed_share"]),
        quote_count_min=int(data["quote_count_min"]),
        quote_count_max=int(data["quote_count_max"]),
        mixed_headlines=list(data["mixed_headlines"]),
        formats=formats,
    )


def _build_style_pack(data: dict[str, Any]) -> StylePack:
    return StylePack(
        name=str(data["name"]),
        image_style=str(data["image_style"]),
        font_paths=list(data.get("font_paths") or []),
        layout=str(data.get("layout", "center")),
        text_color=tuple(int(c) for c in data.get("text_color", (255, 255, 255))),  # type: ignore[arg-type]
        highlight_color=tuple(  # type: ignore[arg-type]
            int(c) for c in data.get("highlight_color", data.get("text_color", (255, 255, 255)))
        ),
        scrim_opacity=int(data.get("scrim_opacity", 140)),
        stroke_width=int(data.get("stroke_width", 0)),
        grade=str(data.get("grade") or ""),
        overlay=str(data.get("overlay", "grain")),
        vignette=bool(data.get("vignette", True)),
        transitions=list(data.get("transitions") or ["fade"]),
        music_mood_anchor=data.get("music_mood_anchor"),
        music_mood_pool=list(data["music_mood_pool"]) if data.get("music_mood_pool") else None,
        voice_id=data.get("voice_id"),
    )


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
            mflux_command=merged["image"]["mflux_command"],
            mflux_model=merged["image"]["mflux_model"],
            mflux_steps=int(merged["image"]["mflux_steps"]),
            mflux_width=int(merged["image"]["mflux_width"]),
            mflux_height=int(merged["image"]["mflux_height"]),
            mflux_timeout_sec=int(merged["image"]["mflux_timeout_sec"]),
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
            contains_synthetic_media=bool(merged["publish"]["contains_synthetic_media"]),
            source_url=merged["publish"]["source_url"] or None,
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
            narration_lufs=float(merged["voiceover"]["narration_lufs"]),
            music_lufs=float(merged["voiceover"]["music_lufs"]),
            fallback_model_id=merged["voiceover"]["fallback_model_id"] or None,
            use_delivery_tags=bool(merged["voiceover"]["use_delivery_tags"]),
            use_timestamps=bool(merged["voiceover"]["use_timestamps"]),
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
        motion=_build_motion(merged["motion"]),
        episodes=_build_episodes(merged["episodes"]),
        stories=_build_stories(merged["stories"], merged["quotes"]["model"]),
        style_packs=[_build_style_pack(pack) for pack in merged["style_packs"]],
        raw=merged,
    )
