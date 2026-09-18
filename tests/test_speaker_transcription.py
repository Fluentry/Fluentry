"""Port of SpeakerTurnMergingTests and the speaker-labelling policy tests."""

import pytest

from fluentry.services.speaker_transcription import (
    SpeakerChunkTranscription,
    SpeakerRecognizedTurn,
    SpeakerTranscriptGap,
    SpeakerTranscriptSegment,
    SpeakerTurn,
    SpeakerTurnTranscription,
    assemble_turns,
    assign_chronological_labels,
    coverage,
    fallback_diagnostic,
    limitation_notice,
    merge_adjacent_turns,
    should_keep_speaker_labels,
    transcribe_chunks,
)


def turn(speaker: str, start: float, end: float) -> SpeakerTurn:
    return SpeakerTurn(speaker_label=speaker, start_seconds=start, end_seconds=end)


def gap(start: float, end: float) -> SpeakerTranscriptGap:
    return SpeakerTranscriptGap(start_seconds=start, end_seconds=end)


# --- merging ----------------------------------------------------------------


def test_empty_input():
    assert merge_adjacent_turns([]) == []


def test_single_turn_is_unchanged():
    merged = merge_adjacent_turns([turn("Speaker 1", 0, 5)])
    assert len(merged) == 1
    assert merged[0].start_seconds == 0
    assert merged[0].end_seconds == 5


def test_same_speaker_across_short_gap_is_merged():
    merged = merge_adjacent_turns([turn("Speaker 1", 0, 5), turn("Speaker 1", 5.5, 9)])
    assert len(merged) == 1
    assert merged[0].end_seconds == 9


def test_same_speaker_across_long_gap_stays_separate():
    merged = merge_adjacent_turns([turn("Speaker 1", 0, 5), turn("Speaker 1", 6.5, 9)])
    assert len(merged) == 2, "1.5s exceeds the 1.0s merge gap"


def test_gap_exactly_at_the_limit_is_merged():
    merged = merge_adjacent_turns([turn("Speaker 1", 0, 5), turn("Speaker 1", 6.0, 9)])
    assert len(merged) == 1


def test_different_speakers_are_never_merged():
    merged = merge_adjacent_turns([turn("Speaker 1", 0, 5), turn("Speaker 2", 5.1, 9)])
    assert len(merged) == 2


def test_collapsed_labels_weld_dialogue_into_one_segment():
    rapid_exchange = [(0, 3), (3.4, 6), (6.3, 9), (9.5, 12), (12.4, 15)]

    all_one_speaker = [turn("Speaker 1", start, end) for start, end in rapid_exchange]
    collapsed = merge_adjacent_turns(all_one_speaker)
    assert len(collapsed) == 1, "one label + short pauses = one block"
    assert collapsed[0].duration_seconds == pytest.approx(15, abs=0.001)

    alternating = [
        turn("Speaker 1" if index % 2 == 0 else "Speaker 2", start, end)
        for index, (start, end) in enumerate(rapid_exchange)
    ]
    assert len(merge_adjacent_turns(alternating)) == 5


def test_overlapping_same_speaker_turn_does_not_shrink_the_merged_range():
    merged = merge_adjacent_turns([turn("Speaker 1", 0, 10), turn("Speaker 1", 5, 8)])
    assert len(merged) == 1
    assert merged[0].start_seconds == 0
    assert merged[0].end_seconds == 10, "a contained turn must not shrink the merged range"


def test_touching_same_speaker_turn_extends_to_end():
    merged = merge_adjacent_turns([turn("Speaker 1", 0, 10), turn("Speaker 1", 9.5, 12)])
    assert len(merged) == 1
    assert merged[0].end_seconds == 12


def test_overlong_run_is_capped_not_merged_indefinitely():
    turns = []
    moment = 0.0
    while moment < 25 * 60:
        turns.append(turn("Speaker 1", moment, moment + 30))
        moment += 30.5

    merged = merge_adjacent_turns(turns)
    assert len(merged) > 1, "a run longer than the 20-minute cap must be split"
    for segment in merged:
        assert segment.duration_seconds <= 20 * 60 + 60


def test_labels_follow_order_of_first_appearance():
    turns = assign_chronological_labels(
        [("cluster-b", 10.0, 12.0), ("cluster-a", 0.0, 5.0), ("cluster-b", 20.0, 22.0)]
    )
    assert [item.speaker_label for item in turns] == ["Speaker 1", "Speaker 2", "Speaker 2"]
    assert turns[0].start_seconds == 0.0


# --- segments ---------------------------------------------------------------


def test_plain_text_includes_minute_timestamp():
    segment = SpeakerTranscriptSegment("Speaker 1", 65.9, 70, "Hello")
    assert segment.plain_text == "[1:05] Speaker 1: Hello"


def test_plain_text_includes_hour_timestamp():
    segment = SpeakerTranscriptSegment("Speaker 2", 3_661, 3_670, "Still here")
    assert segment.plain_text == "[1:01:01] Speaker 2: Still here"


def test_segment_round_trips_through_a_dictionary():
    segment = SpeakerTranscriptSegment("Speaker 1", 0, 5, "Hello")
    assert SpeakerTranscriptSegment.from_dict(segment.to_dict()) == segment


def test_gap_range_text_is_tenth_of_a_second_precise():
    assert gap(10, 10.4).timestamp_range_text == "0:10.0-0:10.4"
    assert gap(65, 70).timestamp_range_text == "1:05.0-1:10.0"


# --- chunk transcription ----------------------------------------------------


def test_empty_later_chunk_keeps_earlier_text_and_records_only_the_missing_range():
    ranges = [gap(0, 1_200), gap(1_200, 1_205)]

    def operation(chunk):
        if chunk.start_seconds == 0:
            return SpeakerChunkTranscription("Recognized first chunk", 0.8)
        return SpeakerChunkTranscription("", 0.1)

    result = transcribe_chunks(ranges, operation)
    assert result.text == "Recognized first chunk"
    assert result.confidence == pytest.approx(0.8)
    assert result.gaps == (ranges[1],)


def test_whitespace_only_chunks_are_gaps_and_confidence_uses_recognized_chunks_only():
    ranges = [gap(0, 0.4), gap(0.4, 4), gap(4, 4.6), gap(4.6, 8)]
    responses = [
        SpeakerChunkTranscription("  \n", 0.1),
        SpeakerChunkTranscription("First", 0.6),
        SpeakerChunkTranscription("\t", 0.2),
        SpeakerChunkTranscription("Second", 1.0),
    ]
    index = 0

    def operation(_chunk):
        nonlocal index
        response = responses[index]
        index += 1
        return response

    result = transcribe_chunks(ranges, operation)
    assert result.text == "First Second"
    assert result.confidence == pytest.approx(0.8)
    assert result.gaps == (ranges[0], ranges[2])


def test_empty_chunks_at_beginning_and_end_keep_middle_text():
    ranges = [gap(0, 0.2), gap(0.2, 9.8), gap(9.8, 10)]
    responses = [None, SpeakerChunkTranscription("Recognized middle", 0.75), SpeakerChunkTranscription("", 0.2)]
    index = 0

    def operation(_chunk):
        nonlocal index
        response = responses[index]
        index += 1
        return response

    result = transcribe_chunks(ranges, operation)
    assert result.text == "Recognized middle"
    assert result.confidence == pytest.approx(0.75)
    assert result.gaps == (ranges[0], ranges[2])


def test_all_empty_chunks_produce_only_gaps():
    ranges = [gap(0, 1), gap(1, 2)]
    result = transcribe_chunks(ranges, lambda _chunk: None)
    assert result.text == ""
    assert result.confidence == 0
    assert result.gaps == tuple(ranges)


def test_provider_error_still_aborts_after_an_earlier_success():
    class StubError(Exception):
        pass

    ranges = [gap(0, 1), gap(1, 2)]
    index = 0

    def operation(_chunk):
        nonlocal index
        index += 1
        if index == 1:
            return SpeakerChunkTranscription("First", 0.9)
        raise StubError()

    with pytest.raises(StubError):
        transcribe_chunks(ranges, operation)


# --- materiality ------------------------------------------------------------


def test_materiality_accepts_exact_limits():
    gaps = [gap(index * 3, (index + 1) * 3) for index in range(10)]
    assert should_keep_speaker_labels(True, gaps, diarized_duration_seconds=3_000)


def test_materiality_accepts_a_small_gap_above_three_seconds_when_total_is_negligible():
    assert should_keep_speaker_labels(True, [gap(10, 13.311)], diarized_duration_seconds=10_000)


def test_materiality_rejects_one_gap_longer_than_five_seconds():
    assert not should_keep_speaker_labels(True, [gap(10, 15.001)], diarized_duration_seconds=10_000)


def test_materiality_accepts_one_gap_at_the_five_second_limit():
    assert should_keep_speaker_labels(True, [gap(10, 15)], diarized_duration_seconds=10_000)


def test_materiality_rejects_more_than_one_percent_omitted():
    gaps = [gap(0, 2.6), gap(10, 12.6), gap(20, 22.6), gap(30, 32.6)]
    assert not should_keep_speaker_labels(True, gaps, diarized_duration_seconds=1_000)


def test_materiality_caps_the_total_allowance_at_thirty_seconds():
    gaps = [gap(index * 4, index * 4 + 3) for index in range(11)]
    assert not should_keep_speaker_labels(True, gaps, diarized_duration_seconds=20_000)


def test_materiality_rejects_an_all_empty_transcript_even_without_gaps():
    assert not should_keep_speaker_labels(False, [], diarized_duration_seconds=60)


def test_materiality_rejects_an_invalid_duration():
    assert not should_keep_speaker_labels(True, [gap(0, 1)], diarized_duration_seconds=0)


def test_coverage_reports_the_evidence_used_by_the_fallback_decision():
    measured = coverage([gap(10, 11.5), gap(20, 24.25)], diarized_duration_seconds=200)

    assert measured.gap_count == 2
    assert measured.skipped_duration_seconds == pytest.approx(5.75)
    assert measured.max_gap_duration_seconds == pytest.approx(4.25)
    assert measured.diarized_duration_seconds == pytest.approx(200)
    assert measured.skipped_ratio == pytest.approx(0.02875)


def test_fallback_diagnostic_names_missing_recognized_text_instead_of_omitted_audio():
    assert (
        fallback_diagnostic(False, [], diarized_duration_seconds=100)
        == "Speaker labeling produced no recognized text"
    )
    assert "invalid diarized duration" in fallback_diagnostic(True, [gap(0, 1)], 0)
    assert "omitted too much audio" in fallback_diagnostic(True, [gap(0, 30)], 100)


def test_speaker_labeling_notice_names_skipped_count_and_duration():
    assert limitation_notice([gap(10, 10.4), gap(20, 21)]) == (
        "Speaker labels were kept, but 2 short audio sections totaling "
        "1.4 seconds produced no text."
    )
    assert limitation_notice([gap(10, 10.4)]).startswith(
        "Speaker labels were kept, but 1 short audio section"
    )
    assert limitation_notice([]) is None


# --- assembly ---------------------------------------------------------------


def recognized(speaker, start, end, text, confidence=0.9, gaps=()):
    return SpeakerRecognizedTurn(
        speaker=speaker,
        start_seconds=start,
        end_seconds=end,
        transcription=SpeakerTurnTranscription(text=text, confidence=confidence, gaps=tuple(gaps)),
    )


def test_tiny_empty_turn_keeps_neighboring_speaker_segments_in_order():
    transcript = assemble_turns(
        [
            recognized("Speaker 1", 0, 10, "Hello"),
            recognized("Speaker 2", 10, 10.2, "", gaps=[gap(10, 10.2)]),
            recognized("Speaker 1", 10.2, 20, "Still here"),
        ]
    )

    assert transcript is not None
    assert [segment.text for segment in transcript.segments] == ["Hello", "Still here"]
    assert transcript.gaps == (gap(10, 10.2),)
    assert transcript.notice is not None


def test_material_empty_turn_rejects_the_labeled_transcript():
    transcript = assemble_turns(
        [
            recognized("Speaker 1", 0, 10, "Hello"),
            recognized("Speaker 2", 10, 40, "", gaps=[gap(10, 40)]),
        ]
    )
    assert transcript is None, "30 seconds of dropped audio is not acceptable"


def test_assembly_returns_nothing_when_no_text_was_recognized():
    assert assemble_turns([recognized("Speaker 1", 0, 10, "")]) is None


def test_assembly_averages_confidence_over_recognized_turns_only():
    transcript = assemble_turns(
        [
            recognized("Speaker 1", 0, 10, "Hello", confidence=1.0),
            recognized("Speaker 2", 10, 20, "There", confidence=0.5),
        ]
    )
    assert transcript.confidence == pytest.approx(0.75)
    assert transcript.notice is None
