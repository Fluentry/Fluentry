"""The interface-localisation runtime and the shipped catalogues.

Two kinds of check live here. The first drives `fluentry.i18n` directly:
locale detection, the stored-preference precedence, and the guarantee that
an untranslated string comes back in English rather than blank. The second
is a contract over every catalogue file the app ships - each must be valid
JSON, and no translation may invent or drop a ``{placeholder}`` its English
source did not have, because a stray or missing placeholder turns into a
``KeyError`` the moment the string is `.format()`-ed at runtime.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from fluentry import i18n

CATALOG_DIR = Path(i18n.__file__).resolve().parent / "resources" / "i18n"
PACKAGE_DIR = Path(i18n.__file__).resolve().parent
PLACEHOLDER = re.compile(r"\{[^}]*\}")


@pytest.fixture(autouse=True)
def _restore_language():
    """Every test starts and ends on English, whatever it switched to."""
    previous = i18n.current_language()
    i18n.set_language("en")
    yield
    i18n.set_language(previous)


# --- runtime ----------------------------------------------------------------


def test_untranslated_string_falls_back_to_english():
    i18n.set_language("es")
    assert i18n.tr("this string has no translation") == "this string has no translation"


def test_english_is_identity():
    i18n.set_language("en")
    assert i18n.tr("Settings") == "Settings"


def test_set_language_rejects_unknown_code():
    assert i18n.set_language("xx") == "en"
    assert i18n.current_language() == "en"


@pytest.mark.parametrize(
    "env,expected",
    [
        ("pt_BR.UTF-8", "pt"),
        ("es", "es"),
        ("fr_FR", "fr"),
        ("zh_CN.UTF-8", "zh"),
        ("C", None),
        ("POSIX", None),
        ("", None),
        ("xx_YY", None),
    ],
)
def test_detect_system_language(monkeypatch, env, expected):
    for name in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LANG", env)
    assert i18n.detect_system_language() == expected


def test_language_env_list_picks_first_supported(monkeypatch):
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(name, raising=False)
    # A language we do not ship, then one we do: the shipped one wins.
    monkeypatch.setenv("LANGUAGE", "xx:de:en")
    assert i18n.detect_system_language() == "de"


def test_resolve_prefers_stored_choice_over_locale(monkeypatch):
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")
    assert i18n.resolve("de") == "de"


def test_resolve_system_sentinel_follows_locale(monkeypatch):
    monkeypatch.setenv("LANGUAGE", "")
    monkeypatch.setenv("LANG", "it_IT.UTF-8")
    assert i18n.resolve("system") == "it"
    assert i18n.resolve(None) == "it"


def test_resolve_falls_back_to_english(monkeypatch):
    for name in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(name, raising=False)
    assert i18n.resolve("system") == "en"


def test_arabic_is_rtl():
    i18n.set_language("ar")
    assert i18n.is_rtl() is True
    i18n.set_language("en")
    assert i18n.is_rtl() is False


# --- shipped catalogues -----------------------------------------------------

SHIPPED = [code for code in i18n.SUPPORTED_UI_LANGUAGES if code != "en"]


def test_every_supported_language_ships_a_catalogue():
    for code in SHIPPED:
        assert (CATALOG_DIR / f"{code}.json").is_file(), f"missing catalogue for {code}"


def _wrapped_source_strings() -> set[str]:
    """Every literal string the code passes to ``tr()``."""

    def literal(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = literal(node.left), literal(node.right)
            if left is not None and right is not None:
                return left + right
        return None

    found: set[str] = set()
    for path in PACKAGE_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "tr"
                and node.args
            ):
                value = literal(node.args[0])
                if value is not None:
                    found.add(value)
    return found


@pytest.mark.parametrize("code", SHIPPED)
def test_catalogue_is_valid_json_of_strings(code):
    data = json.loads((CATALOG_DIR / f"{code}.json").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    for key, value in data.items():
        assert isinstance(key, str) and isinstance(value, str), f"{code}: {key!r}"


@pytest.mark.parametrize("code", SHIPPED)
def test_translations_preserve_placeholders(code):
    """No translation may add or drop a ``{placeholder}`` its source lacked."""
    data = json.loads((CATALOG_DIR / f"{code}.json").read_text(encoding="utf-8"))
    problems = []
    for source, translated in data.items():
        want = set(PLACEHOLDER.findall(source))
        got = set(PLACEHOLDER.findall(translated))
        if want != got:
            problems.append(f"{source!r}: expected {sorted(want)}, got {sorted(got)}")
    assert not problems, f"{code} placeholder mismatch:\n" + "\n".join(problems)


@pytest.mark.parametrize("code", SHIPPED)
def test_catalogue_covers_the_wrapped_literals(code):
    """Most wrapped strings should be translated - guards silent gaps.

    Not every string must be present (a few, such as the "Fluid SFX N"
    sound names, are deliberately left in English), so this asserts broad
    coverage rather than completeness.
    """
    data = json.loads((CATALOG_DIR / f"{code}.json").read_text(encoding="utf-8"))
    wrapped = _wrapped_source_strings()
    covered = wrapped & set(data)
    assert len(covered) >= int(len(wrapped) * 0.9), (
        f"{code} covers only {len(covered)}/{len(wrapped)} wrapped strings"
    )


@pytest.mark.parametrize("code", SHIPPED)
def test_format_calls_survive_translation(code):
    """A translated format template must still accept its placeholders.

    This is the failure the placeholder test guards against, exercised for
    real: every value is `.format`-ed with a dummy for each of its braces.
    """
    data = json.loads((CATALOG_DIR / f"{code}.json").read_text(encoding="utf-8"))
    for translated in data.values():
        names = {name.strip("{}") for name in PLACEHOLDER.findall(translated) if name != "{}"}
        kwargs = {name: "x" for name in names if name.isidentifier()}
        try:
            translated.format(**kwargs)
        except (KeyError, IndexError, ValueError) as error:  # pragma: no cover
            pytest.fail(f"{code}: {translated!r} failed to format: {error}")
