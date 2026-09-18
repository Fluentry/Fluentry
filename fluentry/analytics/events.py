"""Analytics vocabulary.

A port of `AnalyticsEvent.swift`. The event names and property shapes are
unchanged so the two platforms report into the same schema; only `platform`
and `$os` differ.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class AnalyticsEvent(str, Enum):
    ACTIVE_USER = "active_user"
    USAGE_DAILY_SUMMARY = "usage_daily_summary"
    MODEL_USAGE_DAILY_SUMMARY = "model_usage_daily_summary"
    ONBOARDING_STARTED = "onboarding_started"
    ONBOARDING_STEP_VIEWED = "onboarding_step_viewed"
    ONBOARDING_STEP_COMPLETED = "onboarding_step_completed"
    ONBOARDING_COMPLETED = "onboarding_completed"
    ONBOARDING_TRYOUT_FINISHED = "onboarding_tryout_finished"
    MODEL_DOWNLOAD_STARTED = "model_download_started"
    MODEL_DOWNLOAD_FINISHED = "model_download_finished"
    DICTATION_PERFORMANCE_DAILY_SUMMARY = "dictation_performance_daily_summary"


class ActivityKind(str, Enum):
    APP = "app"
    CORE_ACTION = "core_action"


class UsageMode(str, Enum):
    DICTATION = "dictation"
    COMMAND = "command"
    EDIT = "edit"
    MEETING = "meeting"


class ModelRole(str, Enum):
    TRANSCRIPTION = "transcription"
    AI_POST_PROCESSING = "ai_post_processing"


class OnboardingStep(str, Enum):
    WELCOME = "welcome"
    LANGUAGE = "language"
    VOICE_MODEL = "voice_model"
    PERMISSIONS = "permissions"
    PLAYGROUND = "playground"
    AI_ENHANCEMENT = "ai_enhancement"


class OnboardingOrigin(str, Enum):
    FIRST_RUN = "first_run"
    MANUAL_RESTART = "manual_restart"


class OnboardingOutcome(str, Enum):
    CONTINUED = "continued"
    SKIPPED = "skipped"
    COMPLETED = "completed"
    OPENED_SETTINGS = "opened_settings"


class TryoutOutcome(str, Enum):
    SUCCESS = "success"
    EMPTY = "empty"
    ERROR = "error"
    CANCELLED = "cancelled"
    SKIPPED_BEFORE_ATTEMPT = "skipped_before_attempt"
    SKIPPED_AFTER_ATTEMPT = "skipped_after_attempt"


class TryoutStartMethod(str, Enum):
    HOTKEY = "hotkey"
    BUTTON = "button"


class TryoutFailureStage(str, Enum):
    AUDIO_START = "audio_start"
    TRANSCRIPTION = "transcription"
    POST_PROCESSING = "post_processing"


class ModelDownloadSource(str, Enum):
    ONBOARDING = "onboarding"
    SETTINGS = "settings"
    AUTOMATIC = "automatic"


class ModelDownloadOutcome(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True)
class ModelDescriptor:
    provider: str
    model: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _normalized(self.provider))
        object.__setattr__(self, "model", _normalized(self.model))


def _normalized(value: str) -> str:
    normalized = (value or "").strip().lower().replace(" ", "_")
    return normalized or "unknown"


def automatically_collects_dictation_performance(app_version: str) -> bool:
    """Beta builds report performance automatically; stable builds do not.

    The cohort is identified by the *installed* version (1.6.10-beta.1), not
    by the "receive beta updates" preference, which only selects future
    updates.
    """
    tokens = re.split(r"[^0-9a-z]+", (app_version or "").lower())
    return "beta" in tokens


def dictation_summary_line(
    asr_milliseconds: int | None,
    ai_milliseconds: int | None,
    ready_milliseconds: int,
    outcome: str,
) -> str:
    """One stable log line naming the slowest stage of a dictation."""
    asr = asr_milliseconds if asr_milliseconds is not None else -1
    ai = ai_milliseconds if ai_milliseconds is not None else -1
    measured = max(asr, 0) + max(ai, 0)
    app_overhead = max(ready_milliseconds - measured, 0)
    if ai >= asr and ai >= app_overhead and ai >= 0:
        slowest = "ai"
    elif asr >= app_overhead and asr >= 0:
        slowest = "asr"
    else:
        slowest = "app_overhead"
    return (
        f"DICTATION_SUMMARY asrMs={asr} aiMs={ai} "
        f"appOverheadMs={app_overhead} readyMs={ready_milliseconds} "
        f"slowest={slowest} outcome={outcome}"
    )
