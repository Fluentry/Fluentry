"""Browsing, choosing and updating the Private AI model."""

from __future__ import annotations

import pytest

from fluentry.services.private_ai_settings import (
    GIGABYTE,
    MINI_MODEL_ID,
    PICO_MODEL_ID,
    OperationCancelled,
    PrivateAISettingsSession,
    carousel_next,
    carousel_position,
    recommended_model_id,
    run_update_transaction,
)


# --- the recommendation -----------------------------------------------------


@pytest.mark.parametrize("gigabytes", [4, 8, 12])
def test_a_smaller_machine_is_recommended_the_smaller_model(gigabytes):
    assert recommended_model_id(gigabytes * GIGABYTE) == PICO_MODEL_ID


@pytest.mark.parametrize("gigabytes", [16, 24, 32, 64, 128])
def test_a_larger_machine_is_recommended_the_larger_model(gigabytes):
    assert recommended_model_id(gigabytes * GIGABYTE) == MINI_MODEL_ID


def test_the_recommendation_uses_the_exact_boundary():
    assert recommended_model_id(16 * GIGABYTE - 1) == PICO_MODEL_ID
    assert recommended_model_id(16 * GIGABYTE) == MINI_MODEL_ID


# --- the carousel -----------------------------------------------------------


def test_an_empty_catalog_has_nowhere_to_go():
    assert carousel_next([], current="missing", forward=True) is None


def test_one_model_never_fabricates_a_neighbour():
    assert carousel_next(["mini"], current="mini", forward=False) == "mini"


def test_two_models_wrap_in_both_directions():
    assert carousel_next(["pico", "mini"], current="mini", forward=True) == "pico"
    assert carousel_next(["pico", "mini"], current="pico", forward=False) == "mini"


def test_a_stale_preview_recovers_to_a_real_model():
    assert carousel_next(["pico", "mini"], current="deleted", forward=True) == "pico"


def test_three_models_place_their_neighbours_left_and_right():
    catalog = ["pico", "mini", "full"]
    assert carousel_position("pico", catalog, current="mini") == -1
    assert carousel_position("full", catalog, current="mini") == 1
    assert carousel_position("mini", catalog, current="mini") == 0


def test_a_missing_card_has_no_position():
    assert carousel_position("gone", ["pico", "mini"], current="mini") == 0


# --- the session ------------------------------------------------------------


@pytest.fixture
def session() -> PrivateAISettingsSession:
    return PrivateAISettingsSession(selected_model_id="mini")


def test_browsing_follows_the_user_without_activating(session):
    initial = session.revision
    for identifier in ("pico", "mini", "pico"):
        session.preview(identifier)
    assert session.preview_model_id == "pico"
    assert session.selected_model_id == "mini"
    assert session.revision == initial
    assert session.accepts_read(initial) is True


def test_activation_is_explicit_and_invalidates_old_reads(session):
    initial = session.revision
    assert session.select(session.preview_model_id) is True
    assert session.selected_model_id == "mini"

    session.preview("pico")
    assert session.select("pico") is True
    assert session.selected_model_id == "pico"
    assert session.accepts_read(initial) is False


def test_only_one_operation_runs_at_a_time(session):
    first = session.begin()
    assert first is not None
    assert session.begin() is None


def test_activation_waits_for_an_operation_to_finish(session):
    session.begin()
    assert session.select("pico") is False


def test_browsing_stays_available_during_an_operation(session):
    session.begin()
    session.preview("pico")
    assert session.preview_model_id == "pico"
    assert session.selected_model_id == "mini"


def test_an_in_flight_operation_rejects_a_stale_read(session):
    before = session.revision
    session.begin()
    assert session.accepts_read(before) is False


def test_a_foreign_completion_cannot_release_the_slot(session):
    token = session.begin()
    session.finish("some-other-token")
    assert session.is_busy is True
    session.finish(token)
    assert session.is_busy is False


def test_a_late_completion_cannot_release_the_next_operation(session):
    first = session.begin()
    session.finish(first)
    second = session.begin()
    session.finish(first)
    assert session.is_busy is True
    session.finish(second)
    assert session.is_busy is False


def test_a_read_taken_before_an_operation_stays_stale_afterwards(session):
    before = session.revision
    token = session.begin()
    session.finish(token)
    assert session.accepts_read(before) is False


# --- the update transaction -------------------------------------------------


def test_a_successful_update_commits_once_and_never_rolls_back():
    events = []
    committed = []

    result = run_update_transaction(
        update=lambda: (events.append("download"), 7)[1],
        verify=lambda: (events.append("verify"), True)[1],
        commit=lambda token: (committed.append(token), events.append("commit")),
        rollback=lambda token: events.append("rollback"),
    )
    assert result is True
    assert events == ["download", "verify", "commit"]
    assert committed == [7]


def test_a_failed_verification_puts_the_previous_artifact_back():
    events = []
    rolled_back = []

    result = run_update_transaction(
        update=lambda: (events.append("download"), 8)[1],
        verify=lambda: (events.append("verify"), False)[1],
        commit=lambda token: events.append("commit"),
        rollback=lambda token: (rolled_back.append(token), events.append("rollback")),
    )
    assert result is False
    assert events == ["download", "verify", "rollback"]
    assert rolled_back == [8]


def test_a_failed_download_rolls_nothing_back():
    """There is no staged artifact yet, so there is nothing to undo."""
    events = []

    def failing_update():
        events.append("download")
        raise RuntimeError("network died")

    with pytest.raises(RuntimeError):
        run_update_transaction(
            update=failing_update,
            verify=lambda: (events.append("verify"), True)[1],
            commit=lambda token: events.append("commit"),
            rollback=lambda token: events.append("rollback"),
        )
    assert events == ["download"]


def test_cancelling_after_staging_rolls_back_without_verifying():
    events = []

    with pytest.raises(OperationCancelled):
        run_update_transaction(
            update=lambda: (events.append("download"), 9)[1],
            verify=lambda: (events.append("verify"), True)[1],
            commit=lambda token: events.append("commit"),
            rollback=lambda token: events.append("rollback"),
            is_cancelled=lambda: True,
        )
    assert events == ["download", "rollback"]


def test_cancelling_during_verification_never_commits():
    events = []
    cancelled = []

    def verify():
        cancelled.append(True)
        return True

    with pytest.raises(OperationCancelled):
        run_update_transaction(
            update=lambda: 10,
            verify=verify,
            commit=lambda token: events.append("commit"),
            rollback=lambda token: events.append("rollback"),
            is_cancelled=lambda: bool(cancelled),
        )
    assert events == ["rollback"]


def test_the_slot_stays_held_across_the_whole_rollback(session):
    """Releasing early would let a second download start mid-undo."""
    observations = []
    token = session.begin()

    def rollback(_token):
        observations.append((session.is_busy, session.begin(), session.select("pico")))

    run_update_transaction(
        update=lambda: 11,
        verify=lambda: False,
        commit=lambda _token: pytest.fail("must not commit"),
        rollback=rollback,
    )
    assert observations == [(True, None, False)]

    session.finish(token)
    assert session.is_busy is False
