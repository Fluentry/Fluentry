# Fluentry

**Hold a key, talk, and the words are typed into whatever app you are using.**

[Website](https://fluentry.github.io) · [Report an issue](https://github.com/Fluentry/Fluentry/issues)

Fluentry is a dictation app for Linux. Speech recognition runs on your own
machine, so it works offline and nothing you say is sent anywhere unless you
deliberately turn on AI cleanup and point it at a provider.

Built in Python and PySide6 on the interfaces Linux already provides:
PipeWire for audio, evdev for the keyboard, the freedesktop Secret
Service for credentials, MPRIS for media, and Adwaita for the look.

---

## Quick start

On Debian and Ubuntu, install the package from the
[latest release](https://github.com/Fluentry/Fluentry/releases/latest):

```bash
sudo apt install ./fluentry_1.8.1_all.deb
fluentry
```

That pulls in everything the app needs, including `python3-gi` — without
which the permission to type into other apps cannot be remembered and is
asked for again on every launch.

Anywhere else, or to work on it:

```bash
git clone https://github.com/Fluentry/Fluentry.git
cd Fluentry
pip install -e '.[gui,audio,input,whisper,parakeet,libei]'
fluentry --check      # what this machine supports
fluentry              # run it
```

A pip install into a virtualenv cannot see the system `python3-gi`, so
either create the environment with `--system-site-packages` or expect the
permission dialog on every launch.

On first launch a setup flow walks you through picking a language, choosing
and downloading a speech engine, and trying a dictation.

Requires Python 3.11 or newer. The package covers the Parakeet engines.
The Whisper ones need a runtime Debian does not package — either a
`whisper.cpp` binary you already have, or `faster-whisper`, which the app
offers to fetch into `~/.local/share/fluentry/runtimes` the first time you
pick such an engine. Nothing outside that folder is touched, and deleting
it undoes the install.

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
| `libei`    | python-libei            | typing into apps on Wayland            |

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

**Typing into other apps.** Install `python-libei` and your distribution's
`libei` and `python3-gi` packages: libei is the input path a Wayland
compositor is obliged to deliver, and it asks permission once rather than
every launch. `xdotool` reaches XWayland apps only, and `ydotool` — despite
writing to the kernel — has its events read and then discarded by GNOME's
compositor, which looks exactly like success. `wtype` needs the
virtual-keyboard protocol, which GNOME does not offer ordinary clients.
Failing all of those, *Copy to Clipboard Only* always works.

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

An installation from before the rename migrates its old directories
across automatically on first run.

## Architecture

| Concern | How it is served |
|---|---|
| Audio devices | PipeWire (`pw-dump`), PulseAudio (`pactl`), ALSA |
| Capture | PortAudio via `sounddevice`, else `pw-record` / `parec` |
| Global hotkeys | evdev, else pynput on X11 |
| Typing into apps | libei via the RemoteDesktop portal, else `xdotool`, `ydotool` or `wtype` |
| Clipboard | Qt, `wl-copy`, `xclip` or `xsel` |
| Credentials | freedesktop Secret Service (`secret-tool`) |
| Media control | MPRIS via `playerctl` |
| Lid detection | ACPI lid state |
| Launch at login | `~/.config/autostart/fluentry.desktop` |
| Speech | faster-whisper, onnx-asr, sherpa-onnx |
| Settings | JSON under `$XDG_CONFIG_HOME` |
| Interface | PySide6, styled to GNOME's Adwaita conventions |

## Development

```bash
python -m pytest                              # the suite
python -m pytest -m "model or hardware or display"   # the rest
```

Tests needing downloaded weights, real audio hardware or a display are
marked `model`, `hardware` and `display`, and are deselected by default.
The HiDPI tests run in their own process, because the display scale has to
be set before Qt starts.

The website lives in [`site/`](site/); publishing copies it to the
[fluentry.github.io](https://github.com/Fluentry/fluentry.github.io) repository.

## Licence

GPL-3.0-or-later. See [LICENSE](LICENSE).

Fluentry began as a Linux reimplementation of an existing GPL-3.0
dictation app; that lineage is recorded in [NOTICE](NOTICE) as the licence
requires.
