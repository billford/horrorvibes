"""Typed exceptions for horrorvibes.

Library modules raise these instead of calling sys.exit(), so the
orchestrator (and tests) can decide what to do about a failure.
"""


class HorrorVibesError(Exception):
    """Base class for all horrorvibes errors."""


class ConfigError(HorrorVibesError):
    """The config file is missing, malformed, or fails validation."""


class QuoteGenerationError(HorrorVibesError):
    """Quote generation via the chat model failed."""


class ImageGenerationError(HorrorVibesError):
    """Every configured image backend failed for a given quote."""


class MusicGenerationError(HorrorVibesError):
    """Every configured music backend failed."""


class CompositionError(HorrorVibesError):
    """Rendering text onto an image frame failed."""


class VideoAssemblyError(HorrorVibesError):
    """ffmpeg frame/audio assembly failed."""


class PublishError(HorrorVibesError):
    """YouTube upload failed or credentials could not be refreshed."""
