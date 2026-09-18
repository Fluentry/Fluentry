"""The first-run flow, without any of the widgets.

A port of `OnboardingFlowView`'s state: which step is showing, whether the
user may move on from it, and what the two buttons say. Keeping it separate
from the Qt view means the rules that actually matter — you cannot leave the
voice-model step until a model is ready, you cannot leave the playground
while a recording is still running — are testable without a display.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum

from ..analytics.events import OnboardingOutcome, OnboardingStep as AnalyticsStep


class Step(IntEnum):
    LANDING = 0
    LANGUAGE = 1
    VOICE_MODEL = 2
    PERMISSIONS = 3
    PLAYGROUND = 4
    AI_ENHANCEMENT = 5

    @property
    def analytics_step(self) -> AnalyticsStep:
        return {
            Step.LANDING: AnalyticsStep.WELCOME,
            Step.LANGUAGE: AnalyticsStep.LANGUAGE,
            Step.VOICE_MODEL: AnalyticsStep.VOICE_MODEL,
            Step.PERMISSIONS: AnalyticsStep.PERMISSIONS,
            Step.PLAYGROUND: AnalyticsStep.PLAYGROUND,
            Step.AI_ENHANCEMENT: AnalyticsStep.AI_ENHANCEMENT,
        }[self]

    @property
    def title(self) -> str:
        return {
            Step.LANDING: "Welcome",
            Step.LANGUAGE: "Choose Language",
            Step.VOICE_MODEL: "Choose Voice Engine",
            Step.PERMISSIONS: "Enable Access",
            Step.PLAYGROUND: "Try Fluentry",
            Step.AI_ENHANCEMENT: "Set Up AI Enhancement",
        }[self]

    @property
    def subtitle(self) -> str:
        return {
            Step.LANDING: "Talk anywhere. Fluentry types for you.",
            Step.LANGUAGE: "Pick the language you speak most.",
            Step.VOICE_MODEL: "Choose the best local engine for your language.",
            Step.PERMISSIONS: "Allow Fluentry to listen and type into other apps.",
            Step.PLAYGROUND: "Use your dictation shortcut once before finishing setup.",
            Step.AI_ENHANCEMENT: "Optional: Configure AI post-processing or skip this step.",
        }[self]

    @property
    def primary_button_title(self) -> str:
        if self is Step.LANDING:
            return "Next"
        if self is Step.AI_ENHANCEMENT:
            return "Finish Setup"
        return "Continue"


LAST_STEP = Step.AI_ENHANCEMENT


@dataclass
class Readiness:
    """What the surrounding app knows about each gate."""

    has_language_routes: bool = False
    voice_model_ready: bool = False
    model_preparation_in_progress: bool = False
    microphone_ready: bool = False
    typing_ready: bool = False
    ai_ready: bool = False
    playground_ready: bool = False
    is_recording: bool = False
    is_recording_shortcut: bool = False

    @property
    def permissions_ready(self) -> bool:
        return self.microphone_ready and self.typing_ready


class OnboardingFlow:
    """Step navigation, backed by the settings store so it survives a restart."""

    def __init__(self, settings, analytics=None) -> None:
        self.settings = settings
        self.analytics = analytics

    @property
    def step(self) -> Step:
        try:
            return Step(self.settings.onboarding_current_step)
        except ValueError:
            # An out-of-range stored step lands on the voice model, as on macOS.
            return Step.VOICE_MODEL

    @step.setter
    def step(self, value: Step) -> None:
        self.settings.onboarding_current_step = int(value)

    @property
    def progress(self) -> float:
        return float(self.step) / float(LAST_STEP)

    @property
    def compact_progress(self) -> float:
        return float(self.step + 1) / float(LAST_STEP + 1)

    def can_continue(self, readiness: Readiness) -> bool:
        if readiness.model_preparation_in_progress and self.step is Step.VOICE_MODEL:
            return False
        if self.step is Step.LANDING:
            return True
        if self.step is Step.LANGUAGE:
            return readiness.has_language_routes
        if self.step is Step.VOICE_MODEL:
            return readiness.voice_model_ready
        if self.step is Step.PERMISSIONS:
            return readiness.permissions_ready
        if self.step is Step.AI_ENHANCEMENT:
            return readiness.ai_ready
        return (
            readiness.playground_ready
            and not readiness.is_recording
            and not readiness.is_recording_shortcut
        )

    def can_skip(self, readiness: Readiness) -> bool:
        # Skipping mid-recording would leave the capture running behind the flow.
        return not readiness.is_recording and not readiness.is_recording_shortcut

    # --- navigation -------------------------------------------------------

    def go_back(self) -> Step:
        self.step = Step(max(int(Step.LANDING), int(self.step) - 1))
        return self.step

    def go_next(self, outcome: OnboardingOutcome = OnboardingOutcome.CONTINUED) -> Step:
        self._complete_current_step(outcome)
        self.step = Step(min(int(LAST_STEP), int(self.step) + 1))
        return self.step

    def finish(self) -> None:
        origin = self.settings.analytics_onboarding_origin
        self._complete_current_step(
            OnboardingOutcome.COMPLETED, origin=origin, completes_flow=True
        )
        self.settings.onboarding_completed = True

    def skip_ai_enhancement(self) -> None:
        origin = self.settings.analytics_onboarding_origin
        self.settings.onboarding_ai_skipped = True
        self._complete_current_step(
            OnboardingOutcome.SKIPPED, origin=origin, completes_flow=True
        )
        self.settings.onboarding_completed = True

    def skip_playground(self) -> Step:
        self.settings.onboarding_playground_skipped = True
        if self.analytics is not None:
            self.analytics.skip_onboarding_tryout(
                self.settings.analytics_onboarding_origin, datetime.now()
            )
        return self.go_next(OnboardingOutcome.SKIPPED)

    def mark_playground_validated(self) -> None:
        self.settings.onboarding_playground_validated = True
        self.settings.playground_used = True

    def record_step_viewed(self) -> None:
        if self.analytics is None:
            return
        try:
            self.analytics.record_onboarding_step_viewed(
                self.step.analytics_step,
                self.settings.analytics_onboarding_origin,
                datetime.now(),
            )
        except Exception:
            pass  # Analytics must never block setup.

    def _complete_current_step(
        self, outcome: OnboardingOutcome, origin=None, completes_flow: bool = False
    ) -> None:
        if self.analytics is None:
            return
        try:
            self.analytics.record_onboarding_step_completed(
                self.step.analytics_step,
                outcome,
                origin or self.settings.analytics_onboarding_origin,
                completes_flow,
                datetime.now(),
            )
        except Exception:
            pass
