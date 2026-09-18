# Fluentry

Hold a key, talk, and the words are typed into whatever app you are using.

Fluentry is a Linux dictation app, rebuilt from the macOS app **FluidVoice**
on Linux's own audio, input and desktop interfaces: same behaviour, same
settings, same file formats, so a FluidVoice backup restores here.

Everything can run on this machine. No account, no upload, no API key
required.

## Install

    pip install -e '.[gui,audio,input,whisper,parakeet]'

The extras are separate because the core works without them:

| Extra      | Brings in                | Needed for                             |
|------------|--------------------------|----------------------------------------|
| `gui`      | PySide6                  | the tray, the overlay and the windows  |
| `audio`    | sounddevice (PortAudio)  | low-latency capture (else `pw-record`) |
| `input`    | evdev, pynput            | global hotkeys                         |
| `whisper`  | faster-whisper           | the Whisper engines                    |
| `parakeet` | onnx-asr, onnxruntime    | the Parakeet engines                   |
| `onnx`     | sherpa-onnx              | the Nemotron and Cohere engines        |

Then check what this machine supports:

    fluentry --check

That prints a line per capability and exits non-zero if something essential
is missing, so it is also usable from a script.

## Running it

    fluentry                 # tray icon and main window
    fluentry --background    # tray only (what the autostart entry runs)
    fluentry --transcribe recording.wav
    fluentry --version

Install the launcher with the file in `packaging/`; see
[packaging/README.md](packaging/README.md).

## What runs where

macOS frameworks have no Linux equivalents, so each one was replaced with the
interface Linux actually uses:

| On macOS                       | Here                                                     |
|--------------------------------|----------------------------------------------------------|
| CoreAudio device list          | PipeWire (`pw-dump`), PulseAudio (`pactl`), ALSA          |
| AVAudioEngine capture          | PortAudio via `sounddevice`, else `pw-record` / `parec`   |
| CGEventTap hotkeys             | evdev (needs the `input` group), else pynput/X11          |
| Accessibility API typing       | `xdotool`, `ydotool` or `wtype`                           |
| NSPasteboard                   | Qt clipboard, `wl-copy`, `xclip` or `xsel`                |
| Keychain                       | freedesktop Secret Service (`secret-tool`)                |
| MediaRemote                    | MPRIS via `playerctl`                                     |
| Clamshell detection            | ACPI lid state                                            |
| Login item                     | `~/.config/autostart/fluentry.desktop`                  |
| whisper.cpp with CoreML        | faster-whisper (CTranslate2), or a `whisper-cli` binary   |
| FluidAudio CoreML models       | the same checkpoints through ONNX Runtime / sherpa-onnx   |
| UserDefaults                   | `$XDG_CONFIG_HOME/fluentry/settings.json`               |

### Speech models

Each engine needs the runtime that can read its published export, and the
Voice Engine screen marks any engine whose runtime is missing rather than
letting you pick one that can only fail:

| Engine                      | Runtime        | Notes                          |
|-----------------------------|----------------|--------------------------------|
| Whisper Tiny … Large        | faster-whisper | downloads on first use         |
| Parakeet TDT v3 / v2        | onnx-asr       | int8, ~640 MB, multilingual    |
| Nemotron, Cohere Transcribe | sherpa-onnx    | their exports use its layout   |

Model weights are cached under `$XDG_CACHE_HOME/fluentry/models/`, not in
the global Hugging Face cache, so removing Fluentry removes them too.

### Wayland

Wayland deliberately withholds three things a dictation app would like, and
the app reports each one instead of pretending otherwise:

* **Typing into other apps.** `xdotool` reaches XWayland apps only. Install
  `ydotool` (needs a running `ydotoold`) or `wtype` for native Wayland apps.
* **Global hotkeys.** evdev reaches every app but needs your user in the
  `input` group (`sudo usermod -aG input "$USER"`, then log out and back in).
  Without it the app falls back to pynput, which only sees X11/XWayland.
* **The focused window.** Most compositors do not expose it, so per-app
  prompts and app-specific formatting are unavailable there. Hyprland, Sway
  and KWin do expose it and are used when present.

`fluentry --check` tells you which of these apply on your desktop.

## Settings and backups

Settings live in `$XDG_CONFIG_HOME/fluentry/settings.json`, history in a
SQLite database under `$XDG_DATA_HOME/fluentry/`, and models are cached in
`$XDG_CACHE_HOME/fluentry/models/`.

Every persisted key, enum value and backup field name matches the macOS
build, so a backup exported there restores here and vice versa.

## AI enhancement

Optional. When it is on, each transcript is rewritten by a language model
before being typed — local (Ollama, LM Studio) or remote.

A provider is only ever used after it has been verified, and what is
recorded is a hash of the endpoint-and-key pair. Change either one and the
verification lapses, so a rotated key can never be sent to the old endpoint,
and a provider you never tested is never contacted.

## Text insertion modes

| Mode | What it does |
|---|---|
| Clipboard Free Insert | Types directly, falling back to a paste. Fastest. |
| Clipboard Paste | Pastes through a temporary clipboard, then restores yours. |
| Copy to Clipboard Only | Copies and stops, for you to paste. Works in every app, including Wayland windows no typing tool can reach. |

## History retention

History can clear itself: never (the default), at the end of each day, or
after 7, 30 or 90 days. Expired entries take their saved audio with them.

## The local API

Off by default. When enabled it binds to `127.0.0.1:47733` only — never a
network interface — and offers `/v1/health`, `/v1/history`,
`/v1/dictionary/replacements`, `/v1/dictionary/custom-words`,
`/v1/transcribe` and `/v1/postprocess`.

## Tests

    python -m pytest

Tests needing downloaded model weights, real audio hardware or a display are
marked `model`, `hardware` and `display` and are deselected by default. Run
them with `python -m pytest -m "model or hardware or display"`.

## Licence

GPL-3.0-or-later, the same as the original.
