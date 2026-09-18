"""Command line entry point.

`fluentry` with no arguments opens the app. The other modes exist because
a dictation app should be usable from a terminal and from scripts too:
`--check` prints what this machine can and cannot do, and `--transcribe`
runs a file through the same pipeline the UI uses.
"""

from __future__ import annotations

import argparse
import sys

from .app import APP_VERSION


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fluentry", description="Local dictation for Linux."
    )
    parser.add_argument("--version", action="version", version=f"Fluentry {APP_VERSION}")
    parser.add_argument(
        "--background",
        action="store_true",
        help="Start in the tray without opening the main window (used by autostart).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Print what this machine supports, then exit.",
    )
    parser.add_argument(
        "--transcribe",
        metavar="WAV",
        help="Transcribe a WAV file, print the text and exit.",
    )
    return parser


def run_check() -> int:
    from .app import AppState

    state = AppState(start_services=False)
    print(f"Fluentry {APP_VERSION}")
    ok = True
    for label, is_ready, detail in state.readiness_report():
        print(f"  [{'ok' if is_ready else '  '}] {label}: {detail}")
        ok = ok and is_ready
    print(f"  {state.capture_backend_description()}")
    print(f"  {state.media_backend_description()}")
    print(f"  {state.log_location_description()}")
    print(f"  {state.local_api_description()}")
    return 0 if ok else 1


def run_transcribe(path: str) -> int:
    from pathlib import Path

    from .app import AppState
    from .services.providers.whisper import read_wav_as_mono_float
    from .services.text_pipeline import PipelineContext

    audio_path = Path(path).expanduser()
    if not audio_path.is_file():
        print(f"No such file: {audio_path}", file=sys.stderr)
        return 2

    state = AppState(start_services=False)
    state.reload_provider()
    if state.provider is None:
        print("No speech model is available.", file=sys.stderr)
        return 3
    state.provider.prepare()
    result = state.provider.transcribe(
        read_wav_as_mono_float(audio_path),
        language=state.settings.selected_whisper_language_code,
    )
    print(state.pipeline.run(result.text, context=PipelineContext()).final_text)
    return 0


def run_app(background: bool) -> int:
    try:
        from .ui.application import FluentryApplication
    except ImportError as error:
        print(
            f"The graphical interface needs PySide6: {error}\n"
            "Install it with: pip install 'fluentry[gui]'",
            file=sys.stderr,
        )
        return 4

    application = FluentryApplication()
    if background:
        application.start_in_background()
    return application.run()


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.check:
        return run_check()
    if arguments.transcribe:
        return run_transcribe(arguments.transcribe)
    return run_app(arguments.background)


if __name__ == "__main__":
    raise SystemExit(main())
