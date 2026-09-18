# Fluentry

**Hold a key, talk, and the words are typed into whatever app you are using.**

[Website](https://deniswsrosa.github.io/Fluentry/) · [Report an issue](https://github.com/deniswsrosa/Fluentry/issues)

Fluentry is a dictation app for Linux. Speech recognition runs on your own
machine, so it works offline and nothing you say is sent anywhere unless you
deliberately turn on AI cleanup and point it at a provider.

It is a port of the macOS app [FluidVoice](https://github.com/altic-dev/FluidVoice),
rebuilt in Python and PySide6 on Linux's own audio, input and desktop
interfaces. Settings keys, enum values and the backup format are unchanged,
so a backup moves between the two in either direction.

---

## Quick start

```bash
git clone https://github.com/deniswsrosa/Fluentry.git
cd Fluentry
pip install -e '.[gui,audio,input,whisper,parakeet]'
fluentry --check      # what this machine supports
fluentry              # run it
```

On first launch a setup flow walks you through picking a language, choosing
and downloading a speech engine, and trying a dictation.

Requires Python 3.11 or newer.

### Optional extras

The core installs without any of these; each one unlocks a part of the app.

| Extra      | Brings in               | Needed for                             |
|------------|-------------------------|----------------------------------------|
| `gui`      | PySide6                 | the tray, the overlay and the windows  |
| `audio`    | sounddevice (PortAudio) | low-latency capture (else `pw-record`) |
| `input`    | evdev, pynput           | global hotkeys                         |
| `whisper`  | faster-whisper          | the Whisper engines                    |
| `parakeet` | onnx-asr, onnxruntime   | the Parakeet engines                   |
| `onnx`     | sherpa-onnx             | the Nemotron and Cohere engines        |

## Using it

| Command | What it does |
|---|---|
| `fluentry` | Tray icon and main window |
| `fluentry --background` | Tray only, no window — what the autostart entry runs |
| `fluentry --check` | Prints each capability; exits non-zero if something essential is missing |
| `fluentry --transcribe FILE.wav` | Transcribes a file and prints the text |
| `fluentry --version` | Prints the version |

By default **Right Alt** starts and stops dictation. In *automatic* mode you
can also hold it and talk, releasing to stop. Escape cancels a recording
without typing it.

Install the launcher with the file in [`packaging/`](packaging/).

## Speech engines

Every engine runs locally. Weights download on first use into
`$XDG_CACHE_HOME/fluentry/models/`, and removing Fluentry removes them.

| Engine | Runtime | Notes |
|---|---|---|
| Whisper Tiny … Large | faster-whisper | 99 languages, works out of the box |
| Parakeet TDT v3 / v2 | onnx-asr | ~640 MB int8, multilingual, ~300 ms for a short phrase |
| Nemotron, Cohere Transcribe | sherpa-onnx | their exports use that runtime's layout |

The Voice Engine screen marks any engine whose runtime is not installed,
rather than letting you pick one that can only fail.

## Shaping the text

Between the model and your keyboard, a transcript passes through:

1. **Filler-word removal** — drops "um", "uh" and friends.
2. **Custom dictionary** — fixes words the model keeps mishearing. An empty
   replacement deletes the trigger instead.
3. **Spoken punctuation** — "literal comma" becomes ",".
4. **AI cleanup** *(optional, off by default)* — rewrites the transcript with
   a language model, local or remote. Can be forced on or off per
   application, so dictating into a chat client is cleaned up while a coding
   agent gets the raw transcript. Which instructions go with it come from
   the selected prompt profile, a per-app binding, or your default
   override — and *Send custom prompt only* leaves the built-in
   instructions out entirely.
5. **Formatting rules** and **spoken send** — "send it" can press Return.

### Text insertion modes

| Mode | What it does |
|---|---|
| Clipboard Free Insert | Types directly, falling back to a paste. Fastest. |
| Clipboard Paste | Pastes via a temporary clipboard, then restores yours. |
| Copy to Clipboard Only | Copies and stops, for you to paste. Works in every app, including Wayland windows no typing tool can reach. |

## Wayland

Wayland deliberately withholds three things a dictation app would like.
Fluentry reports each rather than failing quietly — `fluentry --check` tells
you which apply to your session.

**Typing into other apps.** `xdotool` reaches XWayland apps only. Install
`ydotool` (with `ydotoold` running) or `wtype` for native Wayland apps, or
use *Copy to Clipboard Only*, which always works.

**Global hotkeys.** A compositor does not hand keystrokes to ordinary
clients, so the shortcut needs to read the keyboard device directly:

```bash
sudo usermod -aG input "$USER"   # then log out and back in
```

That grants your user read access to all input devices — the standard
tradeoff on Wayland. Reversible with `sudo gpasswd -d "$USER" input`. On an
X11 session nothing is needed.

**The focused window.** Most compositors do not expose it, so per-app prompts
and app-specific formatting are unavailable there. Hyprland, Sway and KWin do
expose it and are used when present.

## Privacy

- Speech recognition is local; transcription needs no network.
- History is a SQLite database under `$XDG_DATA_HOME/fluentry/` and can clear
  itself after a day, 7, 30 or 90 days. Expired entries take their audio too.
- API keys go to the freedesktop Secret Service (your keyring), not a file.
- AI cleanup is off by default. A provider is only used after you verify it,
  and what is stored is a hash of the endpoint-and-key pair — change either
  and the verification lapses, so a rotated key is never sent to a stale
  endpoint.
- The local API binds to `127.0.0.1:47733` only, and is off until enabled.
- Analytics are off unless you opt in, and never include what you said.

## Where things live

| Path | Holds |
|---|---|
| `$XDG_CONFIG_HOME/fluentry/settings.json` | every setting |
| `$XDG_DATA_HOME/fluentry/` | history database, analytics |
| `$XDG_CACHE_HOME/fluentry/models/` | downloaded model weights |
| `$XDG_STATE_HOME/fluentry/fluentry.log` | the log |
| `~/.config/autostart/fluentry.desktop` | the "launch at login" entry |

An installation from before the rename migrates its `fluidvoice`
directories across automatically on first run.

## How the port works

| On macOS | In Fluentry |
|---|---|
| CoreAudio device list | PipeWire (`pw-dump`), PulseAudio (`pactl`), ALSA |
| AVAudioEngine capture | PortAudio via `sounddevice`, else `pw-record` / `parec` |
| CGEventTap hotkeys | evdev, else pynput on X11 |
| Accessibility API typing | `xdotool`, `ydotool` or `wtype` |
| NSPasteboard | Qt clipboard, `wl-copy`, `xclip` or `xsel` |
| Keychain | freedesktop Secret Service (`secret-tool`) |
| MediaRemote | MPRIS via `playerctl` |
| Clamshell detection | ACPI lid state |
| Login item | `~/.config/autostart/fluentry.desktop` |
| whisper.cpp with CoreML | faster-whisper, or a `whisper-cli` binary |
| FluidAudio CoreML models | the same checkpoints through ONNX Runtime |
| UserDefaults | JSON under `$XDG_CONFIG_HOME` |
| SwiftUI | PySide6, styled to GNOME's Adwaita conventions |

## Development

```bash
python -m pytest                              # the suite
python -m pytest -m "model or hardware or display"   # the rest
```

Tests needing downloaded weights, real audio hardware or a display are
marked `model`, `hardware` and `display`, and are deselected by default.
The HiDPI tests run in their own process, because the display scale has to
be set before Qt starts.

The website lives in [`site/`](site/) and is published from `docs/`.

## Licence

GPL-3.0-or-later, the same licence as FluidVoice, from which this is
derived. See [LICENSE](LICENSE).
