"""Port of the literal-formatting coverage in DictationE2ETests."""

from fluentry.persistence.settings_store import Keys, SettingsStore
from fluentry.services.literal_formatting import (
    apply_dictation_literal_formatting,
    apply_mention_formatting,
    apply_slash_command_formatting,
    apply_terminal_literal_autocomplete_spacing,
    make_dictation_literal_output_plan,
)

SLACK = {"app_name": "Slack", "bundle_id": "com.slack.Slack"}
CODEX = {"app_name": "Codex", "bundle_id": "dev.openai.codex"}
NOTES = {"app_name": "Notes", "bundle_id": "org.gnome.Notes"}


def test_literal_formatting_is_off_until_enabled(settings: SettingsStore):
    settings.defaults.remove(Keys.literal_dictation_formatting_enabled)
    assert settings.literal_dictation_formatting_enabled is False

    assert apply_slash_command_formatting("slash compact", enabled=False) == "slash compact"
    assert apply_mention_formatting("mention Paul", enabled=False) == "mention Paul"
    assert (
        make_dictation_literal_output_plan("/compact ", enabled=False, **CODEX).plain_text == "/compact "
    )


def test_slash_command_formatting_leaves_non_command_slash_usage_alone():
    text = (
        "Use 1/2 and and/or. Open src slash services. "
        "Go to https slash slash example dot com. Slash and burn."
    )
    assert apply_slash_command_formatting(text) == text


def test_slash_command_formatting_converts_after_a_lead_in_word():
    assert apply_slash_command_formatting("run slash deploy") == "run /deploy"
    assert apply_slash_command_formatting("type forward slash Status") == "type /status"
    # No lead-in word and mid-sentence: left alone.
    assert apply_slash_command_formatting("we slash deploy nightly") == "we slash deploy nightly"
    # A sentence boundary counts as context.
    assert apply_slash_command_formatting("Done. slash deploy") == "Done. /deploy"


def test_slash_command_formatting_converts_a_spaced_literal_slash():
    assert apply_slash_command_formatting("/ deploy") == "/deploy"
    assert apply_slash_command_formatting("1 / 2") == "1 / 2"


def test_mention_formatting_leaves_prose_alone():
    text = "I am at the store. Meet me at lunch. I am at Paul. Look at Paul's message."
    assert apply_mention_formatting(text, **SLACK) == text


def test_explicit_mention_phrases_convert_anywhere():
    assert apply_mention_formatting("mention Paul", **NOTES) == "@Paul"
    assert apply_mention_formatting("tag Sam", **NOTES) == "@Sam"
    assert apply_mention_formatting("at sign Dana", **NOTES) == "@Dana"
    assert apply_mention_formatting("at the rate Dana", **NOTES) == "@Dana"


def test_relaxed_mentions_only_apply_in_chat_apps_after_a_lead_in_word():
    assert apply_mention_formatting("ping At Sam", **SLACK) == "ping @Sam"
    # Same sentence outside a chat app stays prose.
    assert apply_mention_formatting("ping At Sam", **NOTES) == "ping At Sam"
    # No lead-in word: still prose, even in Slack.
    assert apply_mention_formatting("we arrived At Sam", **SLACK) == "we arrived At Sam"


def test_possessive_names_are_never_turned_into_mentions():
    assert apply_mention_formatting("look at Paul's message", **SLACK) == "look at Paul's message"
    assert apply_mention_formatting("mention Paul's message", **NOTES) == "mention Paul's message"


def test_mention_rejects_place_and_time_words():
    assert apply_mention_formatting("ping At Noon", **SLACK) == "ping At Noon"
    assert apply_mention_formatting("mention office", **NOTES) == "mention office"


def test_combined_formatting_applies_commands_then_mentions():
    assert (
        apply_dictation_literal_formatting("run slash deploy and ping At Sam", **SLACK)
        == "run /deploy and ping @Sam"
    )


def test_terminal_literal_autocomplete_spacing_leaves_non_autocomplete_text_alone():
    assert apply_terminal_literal_autocomplete_spacing("/model ", **NOTES) == "/model "
    assert apply_terminal_literal_autocomplete_spacing("Run /status please ", **CODEX) == "Run /status please "
    assert (
        apply_terminal_literal_autocomplete_spacing("@Paul can you check this ", **SLACK)
        == "@Paul can you check this "
    )


def test_terminal_literal_autocomplete_spacing_drops_the_confirming_space():
    assert apply_terminal_literal_autocomplete_spacing("/model ", **CODEX) == "/model"
    assert apply_terminal_literal_autocomplete_spacing("ping @Paul ", **SLACK) == "ping @Paul"


def test_mention_output_plan_does_not_auto_confirm_autocomplete():
    plan = make_dictation_literal_output_plan("@Paul can you check this", **SLACK)
    assert plan.steps == ("@Paul can you check this",)
    assert plan.plain_text == "@Paul can you check this"


def test_mention_output_plan_stays_plain_outside_mention_apps():
    text = "@Paul can you check this"
    assert make_dictation_literal_output_plan(text, **NOTES).steps == (text,)


def test_slash_command_output_plan_does_not_auto_confirm_autocomplete():
    assert make_dictation_literal_output_plan("/goal update the plan", **CODEX).steps == (
        "/goal update the plan",
    )
    assert make_dictation_literal_output_plan("Run /status please", **CODEX).steps == (
        "Run /status please",
    )


def test_linux_chat_and_terminal_apps_are_recognised():
    from fluentry.services.literal_formatting import (
        is_relaxed_mention_app,
        is_slash_command_autocomplete_app,
    )

    assert is_relaxed_mention_app(app_name="Element", bundle_id="im.riot.Riot")
    assert is_relaxed_mention_app(app_name="Mattermost")
    assert is_slash_command_autocomplete_app(app_name="Cursor")
    assert not is_slash_command_autocomplete_app(app_name="GNOME Text Editor")
