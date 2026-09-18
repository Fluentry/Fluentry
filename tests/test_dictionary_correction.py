"""Port of the automatic-dictionary correction and suggestion coverage."""

from datetime import datetime, timedelta, timezone

from fluentry.persistence.defaults import Defaults
from fluentry.services.dictionary_correction import (
    AutomaticDictionaryCorrectionCandidate,
    AutomaticDictionarySuggestionPolicy,
    AutomaticDictionaryTextChange,
    DictionarySuggestionPolicyConfig,
    SuggestionOutcome,
    TextRange,
    change_continues_candidate,
    correction_candidate,
    is_change_inside_inserted_range,
    is_word_continuation_at_inserted_range_end,
    selection_touches_candidate,
    text_change,
)


def at(seconds: float) -> datetime:
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


def range_of(haystack: str, needle: str) -> TextRange:
    location = haystack.index(needle)
    return TextRange(location, len(needle))


def whole(text: str) -> TextRange:
    return TextRange(0, len(text))


# --- detection --------------------------------------------------------------


def test_detects_edited_word_inside_dictation():
    before = "Notes: I met Barad yesterday."
    after = "Notes: I met Barath yesterday."
    candidate = correction_candidate(before, after, range_of(before, "I met Barad yesterday."))

    assert candidate is not None
    assert candidate.heard_text == "Barad"
    assert candidate.corrected_text == "Barath"


def test_detects_insertion_only_spelling_fix():
    before = "Barat joined the call"
    after = "Barath joined the call"
    candidate = correction_candidate(before, after, whole(before))

    assert candidate is not None
    assert candidate.heard_text == "Barat"
    assert candidate.corrected_text == "Barath"


def test_detects_insertion_at_dictation_end():
    before = "Barat"
    after = "Barath"
    inserted = whole(before)
    change = text_change(before, after)

    assert change is not None
    assert is_word_continuation_at_inserted_range_end(change, after, inserted)

    candidate = correction_candidate(before, after, inserted, allows_insertion_at_end=True)
    assert candidate is not None
    assert candidate.heard_text == "Barat"
    assert candidate.corrected_text == "Barath"


def test_rejects_new_word_at_dictation_end():
    before = "Fluentry works"
    after = "Fluentry works well"
    change = text_change(before, after)

    assert change is not None
    assert not is_word_continuation_at_inserted_range_end(change, after, whole(before))


def test_ignores_typing_after_dictation():
    before = "Fluentry works"
    after = "Fluentry works well"
    assert correction_candidate(before, after, whole(before)) is None


def test_allows_continued_correction_at_range_end():
    change = AutomaticDictionaryTextChange(TextRange(5, 0), TextRange(5, 1))
    inserted = TextRange(0, 5)

    assert not is_change_inside_inserted_range(change, inserted)
    assert is_change_inside_inserted_range(change, inserted, allows_insertion_at_end=True)


def test_keeps_waiting_while_caret_touches_corrected_word():
    corrected = TextRange(8, 6)
    assert selection_touches_candidate(TextRange(14, 0), corrected)
    assert not selection_touches_candidate(TextRange(15, 0), corrected)


def test_treats_space_after_word_as_completion():
    change = AutomaticDictionaryTextChange(TextRange(6, 0), TextRange(6, 1))
    corrected = TextRange(0, 6)

    assert not change_continues_candidate(change, "Barath ", corrected)
    assert change_continues_candidate(change, "Baratha", corrected)


def test_ignores_edit_outside_dictation():
    before = "Title: I met Barad"
    after = "Heading: I met Barad"
    assert correction_candidate(before, after, range_of(before, "I met Barad")) is None


def test_detects_case_only_edit():
    before = "Use Dflash today"
    after = "Use DFlash today"
    candidate = correction_candidate(before, after, whole(before))

    assert candidate is not None
    assert candidate.heard_text == "Dflash"
    assert candidate.corrected_text == "DFlash"


def test_ignores_punctuation_and_spacing_only_edit():
    before = "Use Fluid-Voice today"
    after = "Use Fluid Voice today"
    assert correction_candidate(before, after, whole(before)) is None


def test_ignores_single_character_correction():
    before = "Choose k today"
    after = "Choose okay today"
    assert correction_candidate(before, after, whole(before)) is None


def test_identical_text_produces_no_change():
    assert text_change("same", "same") is None


def test_overlong_candidates_are_rejected():
    long_word = "a" * 60
    before = f"Say {long_word} now"
    after = f"Say {long_word}b now"
    assert correction_candidate(before, after, whole(before)) is None


def test_more_than_three_words_is_not_a_candidate():
    # A single changed word is fine; a four-word span is a rewrite, not a mishearing.
    before = "say alpha beta gamma delta end"
    after = "say one two three four end"
    assert correction_candidate(before, after, whole(before)) is None

    assert correction_candidate("say alpha end", "say one end", whole("say alpha end")) is not None


# --- suggestion policy ------------------------------------------------------


def policy(**overrides) -> AutomaticDictionarySuggestionPolicy:
    configuration = DictionarySuggestionPolicyConfig(**overrides)
    return AutomaticDictionarySuggestionPolicy(defaults=Defaults(), configuration=configuration)


def candidate(heard: str, corrected: str) -> AutomaticDictionaryCorrectionCandidate:
    return AutomaticDictionaryCorrectionCandidate(heard_text=heard, corrected_text=corrected)


def test_suggestion_requires_repeated_correction():
    subject = policy()
    pair = candidate("Barad", "Barath")
    now = at(1000)

    assert not subject.should_show(pair, now=now)
    assert subject.should_show(pair, now=now + timedelta(seconds=60))


def test_suggestion_persists_dismissal_cooldown():
    defaults = Defaults()
    configuration = DictionarySuggestionPolicyConfig(
        required_occurrences=1, dismissed_pair_cooldown=timedelta(seconds=100)
    )
    pair = candidate("Barad", "Barath")
    now = at(2000)

    subject = AutomaticDictionarySuggestionPolicy(defaults=defaults, configuration=configuration)
    assert subject.should_show(pair, now=now)
    subject.mark_shown(pair, now=now)
    subject.record(SuggestionOutcome.DISMISSED, pair, now=now)

    restored = AutomaticDictionarySuggestionPolicy(defaults=defaults, configuration=configuration)
    assert not restored.should_show(pair, now=now + timedelta(seconds=50))
    assert restored.should_show(pair, now=now + timedelta(seconds=101))


def test_suggestion_allows_immediate_different_correction():
    subject = policy(required_occurrences=1)
    first = candidate("Claud", "Claude")
    second = candidate("cloud", "Claude")
    now = at(3500)

    assert subject.should_show(first, now=now)
    subject.mark_shown(first, now=now)
    assert subject.should_show(second, now=now + timedelta(seconds=30))


def test_suggestion_stops_after_session_ignore_limit():
    configuration = DictionarySuggestionPolicyConfig(
        required_occurrences=1, dismissed_pair_cooldown=timedelta(0)
    )
    subject = AutomaticDictionarySuggestionPolicy(defaults=Defaults(), configuration=configuration)
    now = at(4000)

    for index in range(configuration.maximum_session_ignores):
        pair = candidate(f"heard {index}", f"corrected {index}")
        moment = now + timedelta(seconds=index)
        assert subject.should_show(pair, now=moment)
        subject.mark_shown(pair, now=moment)
        subject.record(SuggestionOutcome.TIMED_OUT, pair, now=moment)

    assert not subject.should_show(
        candidate("another error", "another word"), now=now + timedelta(seconds=10)
    )


def test_suggestion_never_returns_after_acceptance():
    subject = policy(required_occurrences=1)
    pair = candidate("Barad", "Barath")
    now = at(5000)

    assert subject.should_show(pair, now=now)
    subject.record(SuggestionOutcome.ACCEPTED, pair, now=now)
    assert not subject.should_show(pair, now=now + timedelta(seconds=10_000))


def test_suggestion_dismissal_remains_temporary():
    subject = policy(
        required_occurrences=1,
        dismissed_pair_cooldown=timedelta(0),
        maximum_session_ignores=10,
    )
    pair = candidate("Barad", "Barath")
    now = at(6000)

    for index in range(4):
        moment = now + timedelta(seconds=index)
        assert subject.should_show(pair, now=moment)
        subject.record(SuggestionOutcome.DISMISSED, pair, now=moment)

    assert subject.should_show(pair, now=now + timedelta(seconds=10))


def test_suggestion_counts_different_mishearings_for_same_correction():
    subject = policy()
    now = at(7000)

    assert not subject.should_show(candidate("Barad", "Barath"), required_occurrences=2, now=now)
    assert subject.should_show(
        candidate("Bharat", "Barath"), required_occurrences=2, now=now + timedelta(seconds=10)
    )


def test_suggestion_ignore_only_suppresses_exact_correction():
    subject = policy(required_occurrences=1)
    ignored = candidate("Barad", "Barath")
    alternative = candidate("Bharat", "Barath")
    now = at(8000)

    assert subject.should_show(ignored, now=now)
    subject.record(SuggestionOutcome.IGNORED, ignored, now=now)
    assert not subject.should_show(ignored, now=now + timedelta(seconds=10))
    assert subject.should_show(alternative, now=now + timedelta(seconds=20))


def test_policy_key_separates_heard_and_corrected_text():
    key = AutomaticDictionarySuggestionPolicy.key
    assert key(candidate("ab", "c")) != key(candidate("a", "bc"))


def test_policy_normalization_folds_case_and_diacritics():
    subject = policy(required_occurrences=1)
    now = at(9000)
    assert subject.should_show(candidate("Barád", "Barath"), now=now)
    # The same pair written differently is the same record, so it stays suppressed.
    subject.record(SuggestionOutcome.ACCEPTED, candidate("Barád", "Barath"), now=now)
    assert not subject.should_show(candidate("BARAD", "barath"), now=now + timedelta(seconds=1))
