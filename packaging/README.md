# Packaging files

`dev.fluentry.Fluentry.desktop` is the application launcher. Install it
with:

    install -Dm644 packaging/dev.fluentry.Fluentry.desktop \
        ~/.local/share/applications/dev.fluentry.Fluentry.desktop

The *autostart* entry is a different file and the app writes it itself, to
`$XDG_CONFIG_HOME/autostart/fluentry.desktop`, whenever "Launch at startup"
is switched on. It runs `fluentry --background`, which starts in the tray
without opening a window.
