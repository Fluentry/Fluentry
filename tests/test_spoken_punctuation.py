"""Port of the spoken-punctuation and formatting-action coverage in DictationE2ETests."""

import pytest

from fluentry.persistence.settings_types import (
    DEFAULT_PUNCTUATION_DICTIONARY_RULES,
    DEFAULT_SPOKEN_FORMATTING_ACTION_RULES,
    PunctuationDictionaryRule,
    SpokenFormattingAction,
    SpokenFormattingActionRule,
)
from fluentry.services.spoken_punctuation import apply_spoken_punctuation_formatting


def fmt(text, prefix="literal", rules=None, action_rules=None, **context):
    return apply_spoken_punctuation_formatting(
        text,
        prefix=prefix,
        rules=rules if rules is not None else DEFAULT_PUNCTUATION_DICTIONARY_RULES,
        action_rules=action_rules if action_rules is not None else [],
        **context,
    )


def fmt_with_actions(text, action_rules=None, **kwargs):
    return fmt(
        text,
        action_rules=action_rules if action_rules is not None else DEFAULT_SPOKEN_FORMATTING_ACTION_RULES,
        **kwargs,
    )


def test_spoken_punctuation_formatting_requires_dictionary_prefix():
    assert (
        fmt(
            "Hello literal comma world literal question mark literal open paren yes "
            "literal close paren literal quote done literal quote"
        )
        == 'Hello, world? (yes) "done"'
    )
    assert fmt("Hello comma world question mark") == "Hello comma world question mark"


def test_spoken_punctuation_formatting_converts_code_and_contact_punctuation_with_prefix():
    assert (
        fmt("email literal at the rate example literal dot com literal slash help literal underscore me")
        == "email@example.com/help_me"
    )
    assert (
        fmt(
            "email literal at sign example literal dot com",
            app_name="Codex",
            bundle_id="com.openai.codex",
        )
        == "email@example.com"
    )
    assert fmt("email at sign example") == "email at sign example"
    assert fmt("x literal hyphen ray costs 50 literal percent") == "x-ray costs 50%"
    assert fmt("a literal plus b literal equals c") == "a + b = c"
    assert fmt("plus equal percent") == "plus equal percent"
    assert fmt("literal plus literal equal 50 literal percent") == "+ = 50%"
    assert fmt("plus I need the normal word") == "plus I need the normal word"


def test_spoken_punctuation_formatting_keeps_bare_dot_in_prose():
    assert fmt("the polka dot dress") == "the polka dot dress"
    assert fmt("example literal dot com") == "example.com"
    assert fmt("version 1 literal dot 2") == "version 1.2"


def test_spoken_punctuation_formatting_cleans_generated_comma_noise_with_prefix():
    assert (
        fmt("literal hyphen literal comma literal hyphen literal comma literal hyphen") == "---"
    )
    assert fmt("50 literal comma literal percent") == "50%"
    assert fmt("literal open bracket literal comma literal close bracket") == "[]"
    assert fmt("literal open paren literal comma literal close paren") == "()"
    assert fmt("literal question mark literal comma literal exclamation mark") == "?!"


def test_spoken_punctuation_formatting_preserves_existing_commas_near_symbols():
    assert fmt("Thanks, @Sam") == "Thanks, @Sam"
    assert fmt("Use C++, now") == "Use C++, now"
    assert fmt("-,-,-") == "-,-,-"
    assert fmt("50, %") == "50, %"


def test_spoken_punctuation_formatting_uses_custom_prefix_and_rules():
    rules = [PunctuationDictionaryRule(["right arrow", "arrow"], "->")]
    assert fmt("type right arrow", prefix="type", rules=rules) == "->"
    assert fmt("literal right arrow", prefix="type", rules=rules) == "literal right arrow"
    assert fmt("type comma", prefix="type", rules=rules) == "type comma"


def test_spoken_punctuation_formatting_uses_edited_rules():
    rules = [PunctuationDictionaryRule(["full stop"], ".")]
    assert fmt("literal full stop", rules=rules) == "."
    assert fmt("literal period", rules=rules) == "literal period"


def test_spoken_formatting_actions_use_shared_prefix():
    assert fmt_with_actions("First literal next line second") == "First\nsecond"
    assert fmt_with_actions("First literal next paragraph second") == "First\n\nsecond"
    assert fmt_with_actions("one literal tab two") == "one\ttwo"
    assert fmt_with_actions("one   literal space   two") == "one two"
    assert fmt_with_actions("First next line second") == "First next line second"


def test_spoken_formatting_actions_remove_adjacent_generated_periods_only():
    assert fmt_with_actions("First. literal new line. Second") == "First\nSecond"
    assert fmt_with_actions("First. literal new paragraph. Second") == "First\n\nSecond"
    assert fmt_with_actions("one. literal tab. two") == "one\ttwo"
    assert fmt_with_actions("one. literal space. two") == "one two"
    assert fmt_with_actions("First literal period literal new line Second") == "First.\nSecond"
    assert fmt_with_actions("First literal new line, Second") == "First\nSecond"
    assert fmt_with_actions("First literal new paragraph, Second") == "First\n\nSecond"
    assert fmt_with_actions("First literal new line literal comma Second") == "First\n, Second"
    assert fmt_with_actions("one literal tab, two") == "one\t, two"


def test_spoken_formatting_actions_can_be_customized_and_unset():
    action_rules = [
        SpokenFormattingActionRule(SpokenFormattingAction.NEW_LINE, ["drop down"]),
        SpokenFormattingActionRule(SpokenFormattingAction.TAB, [], is_enabled=True),
        SpokenFormattingActionRule(SpokenFormattingAction.SPACE, ["little gap"], is_enabled=False),
    ]
    assert fmt_with_actions("First literal drop down second", action_rules=action_rules) == "First\nsecond"
    assert fmt_with_actions("literal tab", action_rules=action_rules) == "literal tab"
    assert fmt_with_actions("literal little gap", action_rules=action_rules) == "literal little gap"


def test_ellipsis_is_not_mistaken_for_a_generated_period_beside_an_action():
    assert fmt_with_actions("wait literal ellipsis literal new line then") == "wait...\nthen"


def test_quote_spacing_toggles_between_opening_and_closing():
    assert fmt("she said literal quote hello literal quote loudly") == 'she said "hello" loudly'


def test_apostrophe_is_attached_while_single_quote_toggles():
    assert fmt("it literal apostrophe s fine") == "it's fine"
    assert fmt("say literal single quote hi literal single quote now") == "say 'hi' now"


def test_formatter_is_a_no_op_without_the_prefix_present():
    assert fmt("nothing to convert here") == "nothing to convert here"
    assert fmt("") == ""


def test_left_attached_symbols_bind_to_the_following_word():
    assert fmt("costs literal dollar 40") == "costs $40"
    # Opening brackets keep the space that precedes them, as in "(yes)" above.
    assert fmt("array literal open bracket 0 literal close bracket") == "array [0]"


def test_linux_paths_dictate_cleanly():
    assert fmt("usr literal slash local literal slash bin") == "usr/local/bin"
    assert fmt("etc literal slash fstab") == "etc/fstab"
    assert fmt("opt literal slash tools") == "opt/tools"


def test_linux_file_names_dictate_cleanly():
    assert fmt("edit nginx literal dot conf") == "edit nginx.conf"
    assert fmt("open notes literal dot toml") == "open notes.toml"
