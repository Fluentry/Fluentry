"""Port of MediaPlaybackServiceTests."""

import pytest

from fluentry.services.media_playback import (
    COMMAND_COOLDOWN_SECONDS,
    CommandCompleted,
    CommandFailed,
    MediaPlaybackCommand,
    MediaPlaybackService,
    MediaPlaybackSnapshot,
    QueryUnavailable,
    ScriptedTransport,
    decode_playerctl_output,
)

PAUSE = MediaPlaybackCommand.PAUSE
PLAY = MediaPlaybackCommand.PLAY


def snapshot(playing: bool | None, title: str = "Track", app: str = "spotify", pid: int = 42):
    return MediaPlaybackSnapshot(application_id=app, process_id=pid, title=title, is_playing=playing)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_service(queries=None, commands=None, clock: Clock | None = None):
    transport = ScriptedTransport(queries=queries, commands=commands)
    clock = clock or Clock()
    service = MediaPlaybackService(
        transport=transport, settle=lambda: None, now=clock, log=lambda message: None
    )
    return service, transport, clock


# --- happy path -------------------------------------------------------------


def test_verified_pause_and_resume_are_ordered():
    playing = snapshot(True)
    paused = snapshot(False)
    service, transport, _ = make_service(queries=[playing, paused, paused, playing])

    service.recording_started(1, enabled=True)
    service.wait_until_settled()
    assert transport.sent == [PAUSE]
    assert service.owns_paused_media

    service.session_finished(1)
    service.wait_until_settled()
    assert transport.sent == [PAUSE, PLAY]
    assert not service.owns_paused_media


def test_unavailable_or_paused_media_never_starts_playback():
    service, transport, _ = make_service(queries=[snapshot(False)])
    service.recording_started(1, enabled=True)
    service.wait_until_settled()
    assert transport.sent == []

    service.session_finished(1)
    service.wait_until_settled()
    assert transport.sent == [], "nothing was paused, so nothing is resumed"


def test_disabled_setting_does_no_media_io():
    service, transport, _ = make_service(queries=[snapshot(True)])
    service.recording_started(1, enabled=False)
    service.wait_until_settled()

    assert transport.query_count == 0
    assert transport.sent == []


def test_initial_query_retries_transient_failure():
    service, transport, _ = make_service(
        queries=[
            QueryUnavailable("timed_out"),
            snapshot(True),
            snapshot(False),
        ]
    )
    service.recording_started(1, enabled=True)
    service.wait_until_settled()

    assert transport.sent == [PAUSE]
    assert service.owns_paused_media


def test_initial_query_retries_are_bounded():
    service, transport, _ = make_service(
        queries=[QueryUnavailable("timed_out")] * 5
    )
    service.recording_started(1, enabled=True)
    service.wait_until_settled()

    assert transport.query_count == 3, "three bounded reads, then give up"
    assert transport.sent == []


# --- races ------------------------------------------------------------------


def test_stop_during_query_prevents_late_pause():
    service, transport, _ = make_service()
    transport.hold_query(1)

    service.recording_started(1, enabled=True)
    assert transport.wait_for_query()
    # The hotkey is released while the query is still in flight.
    service.recording_stopped(1)
    transport.release_query(snapshot(True))
    service.wait_until_settled()

    assert transport.sent == [], "a slow query must not pause after release"

    service.session_finished(1)
    service.wait_until_settled()
    assert transport.sent == []


def test_released_recording_does_not_retry_an_unavailable_initial_query():
    service, transport, _ = make_service()
    transport.hold_query(1)

    service.recording_started(1, enabled=True)
    assert transport.wait_for_query()
    service.recording_stopped(1)
    transport.release_query(QueryUnavailable("temporary"))
    service.wait_until_settled()

    assert transport.sent == []
    assert transport.query_count == 1, "a released recording does not retry"


def test_pause_remains_owned_until_transcription_finishes():
    playing = snapshot(True)
    paused = snapshot(False)
    service, transport, _ = make_service(queries=[playing, paused, paused, playing])

    service.recording_started(1, enabled=True)
    service.wait_until_settled()
    assert service.owns_paused_media

    # The hotkey is released but transcription is still running.
    service.recording_stopped(1)
    service.wait_until_settled()
    assert transport.sent == [PAUSE], "the pause is not undone mid-transcription"
    assert service.owns_paused_media

    service.session_finished(1)
    service.wait_until_settled()
    assert transport.sent == [PAUSE, PLAY]


def test_a_new_recording_supersedes_an_old_finish():
    playing = snapshot(True)
    paused = snapshot(False)
    service, transport, _ = make_service(queries=[playing, paused, paused, paused])

    service.recording_started(1, enabled=True)
    service.wait_until_settled()
    service.recording_started(2, enabled=True)
    service.session_finished(1)  # the old session cannot resume the new one's media
    service.wait_until_settled()

    assert PLAY not in transport.sent
    assert service.owns_paused_media


# --- failures ---------------------------------------------------------------


def test_unconfirmed_pause_does_not_resume_or_spam_commands():
    # The pause command "succeeds" but the player never reports paused.
    service, transport, clock = make_service(
        queries=[snapshot(True), snapshot(True), snapshot(True)]
    )
    service.recording_started(1, enabled=True)
    service.wait_until_settled()

    assert transport.sent == [PAUSE]
    assert not service.owns_paused_media, "an unconfirmed pause is never owned"

    service.session_finished(1)
    service.wait_until_settled()
    assert transport.sent == [PAUSE], "nothing owned, so nothing resumed"


def test_cooldown_suppresses_the_next_pause_and_has_a_bounded_exit():
    playing = snapshot(True)
    service, transport, clock = make_service(queries=[playing, playing, playing])
    service.recording_started(1, enabled=True)
    service.wait_until_settled()
    assert transport.sent == [PAUSE]

    # A second recording inside the cooldown must not issue another command.
    service.session_finished(1)
    service.wait_until_settled()
    transport.queries = [playing, playing, playing]
    service.recording_started(2, enabled=True)
    service.wait_until_settled()
    assert transport.sent == [PAUSE], "suppressed during the cooldown"

    # After the cooldown the next recording performs a fresh query.
    service.session_finished(2)
    clock.advance(COMMAND_COOLDOWN_SECONDS + 1)
    transport.queries = [playing, snapshot(False), snapshot(False)]
    service.recording_started(3, enabled=True)
    service.wait_until_settled()
    assert transport.sent == [PAUSE, PAUSE]


def test_command_timeout_still_checks_for_an_applied_pause():
    # The helper times out, but the player did apply the pause.
    service, transport, _ = make_service(
        queries=[snapshot(True), snapshot(False)],
        commands=[CommandFailed("timed_out")],
    )
    service.recording_started(1, enabled=True)
    service.wait_until_settled()

    assert transport.sent == [PAUSE]
    assert service.owns_paused_media, "an applied pause is owned even if the helper failed"


def test_changed_player_or_item_is_not_resumed():
    playing = snapshot(True, title="Track A")
    paused = snapshot(False, title="Track A")
    other = snapshot(False, title="Track B")
    service, transport, _ = make_service(queries=[playing, paused, other])

    service.recording_started(1, enabled=True)
    service.wait_until_settled()
    assert service.owns_paused_media

    service.session_finished(1)
    service.wait_until_settled()
    assert transport.sent == [PAUSE], "a different item must not be started"
    assert not service.owns_paused_media


def test_manual_play_during_dictation_is_not_re_paused_or_resumed():
    playing = snapshot(True)
    paused = snapshot(False)
    service, transport, _ = make_service(queries=[playing, paused, playing])

    service.recording_started(1, enabled=True)
    service.wait_until_settled()

    # The user pressed play themselves; the app must not fight them.
    service.session_finished(1)
    service.wait_until_settled()
    assert transport.sent == [PAUSE]
    assert not service.owns_paused_media


def test_unknown_verification_never_claims_ownership():
    unknown = snapshot(None)
    service, transport, _ = make_service(queries=[snapshot(True), unknown, unknown])

    service.recording_started(1, enabled=True)
    service.wait_until_settled()

    assert transport.sent == [PAUSE]
    assert not service.owns_paused_media


# --- resume outages ---------------------------------------------------------


def test_transient_resume_query_failure_retries_before_playing():
    playing = snapshot(True)
    paused = snapshot(False)
    service, transport, _ = make_service(
        queries=[playing, paused, QueryUnavailable("timed_out"), paused, playing]
    )
    service.recording_started(1, enabled=True)
    service.wait_until_settled()

    service.session_finished(1)
    service.wait_until_settled()
    assert transport.sent == [PAUSE, PLAY]


def test_exhausted_resume_reads_retain_the_pause_for_the_next_session():
    playing = snapshot(True)
    paused = snapshot(False)
    service, transport, _ = make_service(
        queries=[playing, paused] + [QueryUnavailable("timed_out")] * 20
    )
    service.recording_started(1, enabled=True)
    service.wait_until_settled()
    assert service.owns_paused_media

    service.session_finished(1)
    service.wait_until_settled()

    assert PLAY not in transport.sent, "never Play without a matching paused item"
    assert service.owns_paused_media, "ownership is retained for the next session"


def test_a_new_recording_during_an_unavailable_resume_query_retains_the_pause():
    playing = snapshot(True)
    paused = snapshot(False)
    service, transport, _ = make_service(
        queries=[playing, paused] + [QueryUnavailable("timed_out")] * 10
    )
    service.recording_started(1, enabled=True)
    service.wait_until_settled()

    service.session_finished(1)
    service.recording_started(2, enabled=True)
    service.wait_until_settled()

    assert PLAY not in transport.sent
    assert service.owns_paused_media


# --- shutdown ---------------------------------------------------------------


def test_shutdown_restores_a_confirmed_pause_and_rejects_new_starts():
    playing = snapshot(True)
    paused = snapshot(False)
    service, transport, _ = make_service(queries=[playing, paused, paused, playing])

    service.recording_started(1, enabled=True)
    service.wait_until_settled()
    assert service.owns_paused_media

    service.shutdown()
    assert transport.sent == [PAUSE, PLAY]
    assert not service.owns_paused_media

    service.recording_started(2, enabled=True)
    service.wait_until_settled()
    assert transport.sent == [PAUSE, PLAY], "no new work after shutdown"


def test_shutdown_does_not_retry_an_unavailable_query():
    playing = snapshot(True)
    paused = snapshot(False)
    service, transport, _ = make_service(
        queries=[playing, paused] + [QueryUnavailable("timed_out")] * 10
    )
    service.recording_started(1, enabled=True)
    service.wait_until_settled()
    queries_before = transport.query_count

    service.shutdown()
    assert transport.query_count - queries_before <= 3
    assert PLAY not in transport.sent


# --- transport decoding -----------------------------------------------------


def test_raw_query_failures_are_distinct():
    assert decode_playerctl_output(b"") == QueryUnavailable("empty_output")
    assert decode_playerctl_output("   \n") == QueryUnavailable("empty_output")
    assert decode_playerctl_output("No players found") == QueryUnavailable("no_media_reported")
    assert decode_playerctl_output("null") == QueryUnavailable("no_media_reported")
    assert decode_playerctl_output("{not json") == QueryUnavailable("invalid_payload")
    assert decode_playerctl_output(b"\xff\xfe") == QueryUnavailable("invalid_utf8")
    assert decode_playerctl_output('{"pid": 3}') == QueryUnavailable("invalid_payload")
    assert decode_playerctl_output('{"applicationId": "x"}') == QueryUnavailable("invalid_payload")


def test_raw_query_accepts_missing_title_and_reports_explicit_state():
    decoded = decode_playerctl_output(
        '{"applicationId": "spotify", "pid": 7, "title": null, "status": "Playing"}'
    )
    assert decoded == MediaPlaybackSnapshot("spotify", 7, None, True)

    paused = decode_playerctl_output(
        '{"applicationId": "spotify", "pid": 7, "title": "Track", "status": "Paused"}'
    )
    assert paused.is_playing is False

    unknown = decode_playerctl_output(
        '{"applicationId": "spotify", "pid": 7, "title": "Track", "status": "Weird"}'
    )
    assert unknown.is_playing is None


def test_snapshot_matching_compares_player_and_item():
    first = snapshot(True, title="Track A")
    assert first.matches(snapshot(False, title="Track A"))
    assert not first.matches(snapshot(True, title="Track B"))
    assert not first.matches(snapshot(True, title="Track A", app="vlc"))
    assert not first.matches(snapshot(True, title="Track A", pid=99))
