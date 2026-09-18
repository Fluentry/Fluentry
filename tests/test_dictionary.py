"""Port of the custom dictionary, transfer and vocabulary coverage."""

import json

import pytest

from fluentry.persistence.settings_types import CustomDictionaryEntry
from fluentry.services.custom_dictionary import (
    CustomDictionary,
    CustomDictionaryManualEntry,
    apply_custom_dictionary,
    dictionary_labels,
)
from fluentry.services.dictionary_transfer import (
    DictionaryTransferCustomWord,
    DictionaryTransferDocument,
    DictionaryTransferError,
    DictionaryTransferReplacement,
    DictionaryTransferService,
    ImportMode,
    import_state,
)
from fluentry.services.vocabulary_store import (
    VocabularyStore,
    VocabularyTerm,
    normalized_boost_terms,
)


def document(replacements=(), custom_words=()):
    return DictionaryTransferDocument(
        tuple(replacements),
        tuple(
            word if isinstance(word, DictionaryTransferCustomWord) else DictionaryTransferCustomWord(word)
            for word in custom_words
        ),
    )


# --- replacement ------------------------------------------------------------


def test_custom_dictionary_replacement_treats_replacement_text_literally():
    entry = CustomDictionaryEntry(triggers=["dollar path"], replacement=r"$5 \path")
    assert apply_custom_dictionary("Use dollar path now.", [entry]) == r"Use $5 \path now."


def test_custom_dictionary_replacement_matches_punctuation_triggers():
    entry = CustomDictionaryEntry(triggers=[",,", ","], replacement=",")
    dictionary = CustomDictionary([entry])
    assert dictionary.apply("Hello,, world.") == "Hello, world."
    assert dictionary.apply("Hello, world.") == "Hello, world."


def test_instant_replacement_still_requires_exact_whole_word_trigger():
    dictionary = CustomDictionary([CustomDictionaryEntry(triggers=["sean"], replacement="Shaun")])
    assert dictionary.apply("Did you mean Monday?") == "Did you mean Monday?"
    assert dictionary.apply("Ask sean Monday.") == "Ask Shaun Monday."


def test_whitespace_replacements_own_adjacent_horizontal_separators():
    dictionary = CustomDictionary(
        [
            CustomDictionaryEntry(triggers=["new line"], replacement="\n"),
            CustomDictionaryEntry(triggers=["new paragraph"], replacement="\n\n"),
            CustomDictionaryEntry(triggers=["tab over"], replacement="\t"),
            CustomDictionaryEntry(triggers=["little space"], replacement=" "),
        ]
    )
    assert dictionary.apply("first new line second") == "first\nsecond"
    assert dictionary.apply("first  new paragraph  second") == "first\n\nsecond"
    assert dictionary.apply("first tab over second") == "first\tsecond"
    assert dictionary.apply("first   little space   second") == "first second"
    assert dictionary.apply("first\n  new line  second") == "first\n\nsecond"


def test_dictionary_is_a_no_op_with_no_entries():
    assert apply_custom_dictionary("unchanged", []) == "unchanged"


def test_dictionary_cache_rebuilds_after_entries_change():
    dictionary = CustomDictionary([CustomDictionaryEntry(triggers=["a"], replacement="A")])
    assert dictionary.apply("a") == "A"
    dictionary.entries = [CustomDictionaryEntry(triggers=["a"], replacement="Z")]
    assert dictionary.apply("a") == "Z"


def test_pronunciation_dictionary_labels_use_last_duplicate_entry():
    identifier = "SHARED-ID"
    labels = dictionary_labels(
        [
            CustomDictionaryEntry(id=identifier, triggers=["old"], replacement="Old"),
            CustomDictionaryEntry(id=identifier, triggers=["new"], replacement="New"),
        ]
    )
    assert labels == {identifier: "New"}


# --- manual entry -----------------------------------------------------------


def test_manual_entry_keeps_whitespace_only_replacements():
    sanitize = CustomDictionaryManualEntry.sanitized_replacement
    assert sanitize("\n") == "\n"
    assert sanitize(" ") == " "
    assert sanitize("\t") == "\t"
    assert sanitize("") == ""
    assert sanitize("  Fluentry \n") == "Fluentry"


def test_manual_entry_renders_whitespace_replacements_visibly():
    render = CustomDictionaryManualEntry.replacement_display_text
    assert render("\n") == "⏎"
    assert render(" ") == "␣"
    assert render("\t") == "⇥"
    assert render(" \n") == "␣⏎"
    assert render("Fluentry") == "Fluentry"
    assert render("") == ""


def test_manual_dictionary_entry_lowercases_and_trims_triggers():
    entry = CustomDictionaryEntry(triggers=["  Fluid Voice ", "FLUID BOYS"], replacement="Fluentry")
    assert entry.triggers == ["fluid voice", "fluid boys"]


# --- transfer ---------------------------------------------------------------


def test_dictionary_transfer_document_encodes_simple_user_format():
    service = DictionaryTransferService()
    encoded = service.encode(
        document(
            [DictionaryTransferReplacement(("fluid voice", "fluid boys"), "Fluentry")],
            ["Fluentry", "GEMBA-E"],
        )
    )
    text = encoded.decode("utf-8")
    root = json.loads(text)

    assert root["replacements"][0]["from"] == ["fluid voice", "fluid boys"]
    assert root["replacements"][0]["to"] == "Fluentry"
    assert root["customWords"] == ["Fluentry", "GEMBA-E"]
    assert '"triggers"' not in text
    assert '"replacement"' not in text
    assert '"aliases"' not in text


def test_dictionary_transfer_import_replace_maps_simple_format_to_stores():
    state = import_state(
        document(
            [DictionaryTransferReplacement((" Fluid Voice ", "FLUID BOYS", ""), " Fluentry ")],
            [" Fluentry ", "fluentry", " Barath "],
        ),
        ImportMode.REPLACE,
        current_replacements=[CustomDictionaryEntry(triggers=["old"], replacement="Old")],
        current_custom_words=[VocabularyTerm("OldWord", 13.0)],
    )

    assert len(state.replacements) == 1
    assert state.replacements[0].triggers == ["fluid voice", "fluid boys"]
    assert state.replacements[0].replacement == "Fluentry"
    assert [term.text for term in state.custom_words] == ["Fluentry", "Barath"]
    assert [term.weight for term in state.custom_words] == [10.0, 10.0]
    assert [term.aliases for term in state.custom_words] == [(), ()]


def test_dictionary_transfer_import_merge_dedupes_and_moves_duplicate_triggers():
    state = import_state(
        document(
            [DictionaryTransferReplacement(("fluid voice", "fluid boys"), "Fluentry")],
            ["barath", "GEMBA-E"],
        ),
        ImportMode.MERGE,
        current_replacements=[
            CustomDictionaryEntry(triggers=["fluid voice", "old trigger"], replacement="Old"),
            CustomDictionaryEntry(triggers=["fluid boys"], replacement="Fluentry"),
        ],
        current_custom_words=[VocabularyTerm("Barath", 13.0, ("barath w",))],
    )

    fluid = next(entry for entry in state.replacements if entry.replacement == "Fluentry")
    old = next(entry for entry in state.replacements if entry.replacement == "Old")
    barath = next(term for term in state.custom_words if term.text == "Barath")
    gembae = next(term for term in state.custom_words if term.text == "GEMBA-E")

    assert set(fluid.triggers) == {"fluid voice", "fluid boys"}
    assert old.triggers == ["old trigger"]
    assert barath.weight == 13.0
    assert barath.aliases == ("barath w",)
    assert gembae.weight == 10.0


def test_dictionary_transfer_import_accepts_app_style_keys_and_single_from_value():
    payload = """
    {
      "replacements": [
        {"from": "fluid voice", "to": "Fluentry"},
        {"triggers": ["gemba e"], "replacement": "GEMBA-E"}
      ]
    }
    """
    decoded = DictionaryTransferService().decode(payload)
    state = import_state(decoded, ImportMode.REPLACE)

    assert [entry.triggers for entry in state.replacements] == [["fluid voice"], ["gemba e"]]
    assert [entry.replacement for entry in state.replacements] == ["Fluentry", "GEMBA-E"]


def test_dictionary_transfer_import_accepts_local_api_replacement_items_response():
    payload = """
    {"count": 1, "items": [{"triggers": ["fluid voice"], "replacement": "Fluentry"}]}
    """
    state = import_state(DictionaryTransferService().decode(payload), ImportMode.REPLACE)

    assert state.replacements[0].triggers == ["fluid voice"]
    assert state.replacements[0].replacement == "Fluentry"
    assert len(state.custom_words) == 0


def test_dictionary_transfer_import_rejects_invalid_replacement_trigger_type():
    with pytest.raises(DictionaryTransferError):
        DictionaryTransferService().decode('{"replacements": [{"from": 42, "to": "Fluentry"}]}')


def test_dictionary_transfer_import_rejects_an_unrecognised_document():
    with pytest.raises(DictionaryTransferError):
        DictionaryTransferService().decode('{"unrelated": true}')
    with pytest.raises(DictionaryTransferError):
        DictionaryTransferService().decode("not json")


def test_dictionary_transfer_import_accepts_a_vocabulary_terms_file():
    payload = """
    {
      "alpha": 2.8,
      "terms": [
        {"text": "Fluentry", "aliases": ["fluid voice"], "weight": 13.0},
        {"text": "GEMBA-E"}
      ]
    }
    """
    state = import_state(DictionaryTransferService().decode(payload), ImportMode.REPLACE)

    assert len(state.replacements) == 0
    assert [term.text for term in state.custom_words] == ["Fluentry", "GEMBA-E"]
    assert [term.weight for term in state.custom_words] == [13.0, 10.0]
    assert [term.aliases for term in state.custom_words] == [(), ()]


def test_dictionary_transfer_import_accepts_local_api_custom_words_response():
    payload = """
    {
      "count": 2,
      "items": [
        {"text": "Fluentry", "weight": 10.0, "aliases": ["fluid voice"]},
        {"text": "Barath"}
      ]
    }
    """
    state = import_state(DictionaryTransferService().decode(payload), ImportMode.REPLACE)

    assert len(state.replacements) == 0
    assert [term.text for term in state.custom_words] == ["Fluentry", "Barath"]
    assert [term.weight for term in state.custom_words] == [10.0, 10.0]


def test_dictionary_transfer_import_feeds_the_actual_replacement_path():
    state = import_state(
        document([DictionaryTransferReplacement(("fluid voice",), "Fluentry")]),
        ImportMode.REPLACE,
    )
    dictionary = CustomDictionary(list(state.replacements))
    assert dictionary.apply("I use fluid voice daily.") == "I use Fluentry daily."


def test_whitespace_replacement_survives_transfer_and_replacement():
    service = DictionaryTransferService()
    decoded = service.decode(
        service.encode(document([DictionaryTransferReplacement(("new line",), "\n")]))
    )
    state = import_state(decoded, ImportMode.REPLACE)

    assert state.replacements[0].replacement == "\n"
    assert CustomDictionary(list(state.replacements)).apply("first new line second") == "first\nsecond"


def test_transfer_still_rejects_empty_and_trims_visible_replacement():
    service = DictionaryTransferService()
    decoded = service.decode(
        service.encode(
            document(
                [
                    DictionaryTransferReplacement(("empty",), ""),
                    DictionaryTransferReplacement(("fluid voice",), " Fluentry \n"),
                ]
            )
        )
    )
    assert len(decoded.replacements) == 1
    assert decoded.replacements[0].to == "Fluentry"


def test_suggested_dictionary_filename_uses_minute_resolution():
    from datetime import datetime, timezone

    moment = datetime(2026, 3, 8, 18, 5, tzinfo=timezone.utc)
    assert (
        DictionaryTransferService().suggested_filename(moment)
        == "Fluentry_Dictionary_2026-03-08_18-05.json"
    )


# --- vocabulary store -------------------------------------------------------


def test_instant_replacement_does_not_enter_boost_vocabulary():
    explicit = VocabularyTerm("Fluentry", 10.0, ("fluid voice",))
    terms = normalized_boost_terms([explicit])

    assert [term.text for term in terms] == ["Fluentry"]
    assert terms[0].aliases == ("fluid voice",)
    # An instant replacement ("sean" → "Shaun") is never a boost candidate.
    assert not any(term.text.casefold() == "shaun" for term in terms)
    assert not any("sean" in term.aliases for term in terms)


def test_boost_terms_merge_duplicates_and_keep_the_highest_weight():
    terms = normalized_boost_terms(
        [
            VocabularyTerm("Fluentry", 4.0, ("fluid voice",)),
            VocabularyTerm("fluentry", 11.0, ("fluid boys",)),
            VocabularyTerm("  ", None),
        ]
    )
    assert len(terms) == 1
    assert terms[0].text == "Fluentry"
    assert terms[0].weight == 11.0
    assert terms[0].aliases == ("fluid boys", "fluid voice")


def test_boost_terms_drop_an_alias_equal_to_the_term_itself():
    terms = normalized_boost_terms([VocabularyTerm("Fluentry", None, ("FLUENTRY", "fluid voice"))])
    assert terms[0].aliases == ("fluid voice",)


def test_vocabulary_store_round_trips_terms_and_keeps_tuning_backend_controlled(tmp_path):
    store = VocabularyStore(path=tmp_path / "vocab.json")
    store.save_user_boost_terms([VocabularyTerm("GEMBA-E", 7.0, ("gemba e",))])

    payload = json.loads(store.load_raw_json())
    assert payload["alpha"] == 2.8
    assert payload["minTermLength"] == 3
    assert payload["terms"] == [{"text": "GEMBA-E", "weight": 7.0, "aliases": ["gemba e"]}]

    loaded = store.load_user_boost_terms()
    assert loaded == [VocabularyTerm("GEMBA-E", 7.0, ("gemba e",))]


def test_vocabulary_store_seeds_a_default_template(tmp_path):
    store = VocabularyStore(path=tmp_path / "vocab.json")
    assert [term.text for term in store.load_user_boost_terms()] == ["Fluentry"]
    assert store.has_any_boost_terms()


def test_vocabulary_store_rejects_malformed_json(tmp_path):
    from fluentry.services.vocabulary_store import InvalidVocabularyJSONError

    store = VocabularyStore(path=tmp_path / "vocab.json")
    with pytest.raises(InvalidVocabularyJSONError):
        store.save_raw_json("{ not json")
    with pytest.raises(InvalidVocabularyJSONError):
        store.save_raw_json('{"terms": [{"weight": 3}]}')


def test_vocabulary_store_ignores_stale_tuning_from_an_old_file(tmp_path):
    path = tmp_path / "vocab.json"
    path.write_text(json.dumps({"alpha": 99.0, "minTermLength": 1, "terms": [{"text": "X"}]}))
    resolved = VocabularyStore(path=path).load_resolved_config()
    assert resolved.alpha == 2.8
    assert resolved.min_term_length == 3
    assert [term.text for term in resolved.terms] == ["X"]


def test_vocabulary_store_prioritizes_heavier_terms_when_capping(tmp_path):
    store = VocabularyStore(path=tmp_path / "vocab.json")
    store.save_user_boost_terms(
        [VocabularyTerm("light", 1.0), VocabularyTerm("heavy", 20.0), VocabularyTerm("mid", 5.0)]
    )
    assert [term.text for term in store.prioritized_terms(2)] == ["heavy", "mid"]


def test_vocabulary_store_caps_the_user_term_list(tmp_path):
    from fluentry.services.vocabulary_store import MAX_TERMS, normalize_user_terms

    terms = [VocabularyTerm(f"term{index}") for index in range(MAX_TERMS + 50)]
    assert len(normalize_user_terms(terms)) == MAX_TERMS
