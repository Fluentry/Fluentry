# Packaging files

`dev.fluentry.Fluentry.desktop` is the application launcher. Install it
with:

    install -Dm644 packaging/dev.fluentry.Fluentry.desktop \
        ~/.local/share/applications/dev.fluentry.Fluentry.desktop

The *autostart* entry is a different file. The .deb ships one system-wide
as `/etc/xdg/autostart/fluentry.desktop` (from
`packaging/fluentry-autostart.desktop`), so a fresh install starts in the
tray at every login without any setup. It runs `fluentry --background`,
which starts in the tray without opening a window.

The in-app "Start Fluentry at login" toggle overrides the system entry
per user by writing `$XDG_CONFIG_HOME/autostart/fluentry.desktop` — the
same basename, which is how XDG autostart overriding works — either with
a real entry (on) or with `Hidden=true` (off).

## Icons

The application icon is installed from `fluentry/resources/` into the
hicolor theme, which is where `Icon=dev.fluentry.Fluentry` in the desktop
entry resolves from:

    for size in 16 22 24 32 48 64 128 256; do
      install -Dm644 "fluentry/resources/icon-$size.png" \
        "$HOME/.local/share/icons/hicolor/${size}x${size}/apps/dev.fluentry.Fluentry.png"
    done
    gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true

Run `python packaging/make_assets.py` to regenerate every derived image
after changing `logo.png` or `logo_image.png`.
