# Packaging files

`dev.fluentry.Fluentry.desktop` is the application launcher. Install it
with:

    install -Dm644 packaging/dev.fluentry.Fluentry.desktop \
        ~/.local/share/applications/dev.fluentry.Fluentry.desktop

The *autostart* entry is a different file and the app writes it itself, to
`$XDG_CONFIG_HOME/autostart/fluentry.desktop`, whenever "Launch at startup"
is switched on. It runs `fluentry --background`, which starts in the tray
without opening a window.

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
