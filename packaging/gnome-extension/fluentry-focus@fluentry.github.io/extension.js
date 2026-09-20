// Fluentry Focus — exposes the focused window over the session bus.
//
// GNOME Wayland withholds the focused window from ordinary clients
// (org.gnome.Shell.Introspect.GetWindows is allowed only to portals, and
// Eval is disabled), so an app cannot tell whether it is typing into a
// terminal - which needs Ctrl+Shift+V - or anything else. This publishes
// exactly the focused window's class and title, and nothing more.
import Gio from 'gi://Gio';

const IFACE = `
<node>
  <interface name="org.fluentry.Focus">
    <method name="GetFocused">
      <arg type="s" direction="out" name="app_id"/>
      <arg type="s" direction="out" name="title"/>
    </method>
  </interface>
</node>`;

export default class FluentryFocusExtension {
    enable() {
        this._impl = Gio.DBusExportedObject.wrapJSObject(IFACE, this);
        this._impl.export(Gio.DBus.session, '/org/fluentry/Focus');
        this._ownerId = Gio.bus_own_name(
            Gio.BusType.SESSION,
            'org.fluentry.Focus',
            Gio.BusNameOwnerFlags.NONE,
            null, null, null,
        );
    }

    disable() {
        if (this._impl) {
            this._impl.unexport();
            this._impl = null;
        }
        if (this._ownerId) {
            Gio.bus_unown_name(this._ownerId);
            this._ownerId = 0;
        }
    }

    GetFocused() {
        const win = global.display.focus_window;
        if (!win)
            return ['', ''];
        return [win.get_wm_class() || '', win.get_title() || ''];
    }
}
