#!/usr/bin/env bash
# Build a .deb that installs a working Fluentry.
#
# The point of this package is that `apt install ./fluentry_*.deb` leaves
# nothing for the user to do: every library the app needs on Wayland comes
# from the archive, including python3-gi, without which the permission to
# type into other apps cannot be remembered and is asked for on every
# launch.
#
#     packaging/build-deb.sh            # -> dist/fluentry_<version>_all.deb
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$ROOT/pyproject.toml")"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

SITE="$STAGE/usr/lib/python3/dist-packages"
mkdir -p "$SITE" "$STAGE/usr/bin" "$STAGE/DEBIAN" \
         "$STAGE/usr/share/applications" "$STAGE/usr/share/doc/fluentry"

# The app itself.
cp -r "$ROOT/fluentry" "$SITE/"
find "$SITE/fluentry" -name '__pycache__' -type d -prune -exec rm -rf {} +

# Two dependencies Debian does not package. Both are pure Python, so they
# are carried here rather than asking the user to reach for pip: onnx-asr
# runs the default speech model, and python-libei is the input path.
python3 -m pip install --quiet --no-compile --target "$SITE" \
    --no-deps "onnx-asr>=0.12" "python-libei>=0.5"
# The .dist-info directories stay: onnx_asr reads its own version through
# importlib.metadata at import time, and without that metadata the import
# raises PackageNotFoundError and the engine looks unavailable while its
# weights sit on disk.
# pip drops console scripts into a bin/ inside the target directory; they
# are not ours to install and would collide on the python path.
rm -rf "$SITE/bin"
find "$SITE" -name '__pycache__' -type d -prune -exec rm -rf {} +

cat > "$STAGE/usr/bin/fluentry" <<'LAUNCHER'
#!/usr/bin/env python3
from fluentry.__main__ import main

raise SystemExit(main())
LAUNCHER
chmod 755 "$STAGE/usr/bin/fluentry"

install -Dm644 "$ROOT/packaging/dev.fluentry.Fluentry.desktop" \
    "$STAGE/usr/share/applications/dev.fluentry.Fluentry.desktop"
# Start in the tray at every login, out of the box. The basename must stay
# `fluentry.desktop`: the in-app "Start Fluentry at login" toggle overrides
# this entry by writing a file of the same name (Hidden=true to turn it
# off) under ~/.config/autostart.
install -Dm644 "$ROOT/packaging/fluentry-autostart.desktop" \
    "$STAGE/etc/xdg/autostart/fluentry.desktop"
for size in 16 22 24 32 48 64 128 256; do
    icon="$ROOT/fluentry/resources/icon-$size.png"
    [ -f "$icon" ] && install -Dm644 "$icon" \
        "$STAGE/usr/share/icons/hicolor/${size}x${size}/apps/dev.fluentry.Fluentry.png"
done
# The GNOME Shell extension that reveals the focused window, installed
# system-wide so every user has it available to enable.
EXT="fluentry-focus@fluentry.github.io"
EXT_DEST="$STAGE/usr/share/gnome-shell/extensions/$EXT"
mkdir -p "$EXT_DEST"
install -Dm644 "$ROOT/packaging/gnome-extension/$EXT/metadata.json" "$EXT_DEST/metadata.json"
install -Dm644 "$ROOT/packaging/gnome-extension/$EXT/extension.js" "$EXT_DEST/extension.js"

install -Dm644 "$ROOT/LICENSE" "$STAGE/usr/share/doc/fluentry/copyright"
[ -f "$ROOT/NOTICE" ] && install -Dm644 "$ROOT/NOTICE" "$STAGE/usr/share/doc/fluentry/NOTICE"

sed "s/@VERSION@/$VERSION/" "$ROOT/packaging/deb/control" > "$STAGE/DEBIAN/control"
install -Dm755 "$ROOT/packaging/deb/postinst" "$STAGE/DEBIAN/postinst"
install -Dm755 "$ROOT/packaging/deb/postrm" "$STAGE/DEBIAN/postrm"

# Prove the staged tree actually works before wrapping it up. Stripping
# the package metadata once made onnx_asr unimportable, and the only
# symptom was an engine reporting itself unavailable while its weights sat
# on disk - nothing a unit test in the source tree would ever have caught.
for module in fluentry onnx_asr libei; do
    if ! PYTHONPATH="$SITE" python3 -c "import $module" 2>/dev/null; then
        echo "staged package is broken: $module does not import" >&2
        PYTHONPATH="$SITE" python3 -c "import $module" >&2 || true
        exit 1
    fi
done

mkdir -p "$ROOT/dist"
OUT="$ROOT/dist/fluentry_${VERSION}_all.deb"
dpkg-deb --root-owner-group --build "$STAGE" "$OUT" >/dev/null
echo "$OUT"
