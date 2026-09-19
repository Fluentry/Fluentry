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
find "$SITE" -name '*.dist-info' -type d -prune -exec rm -rf {} +
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
for size in 16 22 24 32 48 64 128 256; do
    icon="$ROOT/fluentry/resources/icon-$size.png"
    [ -f "$icon" ] && install -Dm644 "$icon" \
        "$STAGE/usr/share/icons/hicolor/${size}x${size}/apps/dev.fluentry.Fluentry.png"
done
install -Dm644 "$ROOT/LICENSE" "$STAGE/usr/share/doc/fluentry/copyright"
[ -f "$ROOT/NOTICE" ] && install -Dm644 "$ROOT/NOTICE" "$STAGE/usr/share/doc/fluentry/NOTICE"

sed "s/@VERSION@/$VERSION/" "$ROOT/packaging/deb/control" > "$STAGE/DEBIAN/control"
install -Dm755 "$ROOT/packaging/deb/postinst" "$STAGE/DEBIAN/postinst"
install -Dm755 "$ROOT/packaging/deb/postrm" "$STAGE/DEBIAN/postrm"

mkdir -p "$ROOT/dist"
OUT="$ROOT/dist/fluentry_${VERSION}_all.deb"
dpkg-deb --root-owner-group --build "$STAGE" "$OUT" >/dev/null
echo "$OUT"
