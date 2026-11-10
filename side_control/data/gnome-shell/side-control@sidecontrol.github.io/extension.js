// SPDX-License-Identifier: GPL-3.0-or-later
// Side Control's GNOME Shell extension: the PC pointer's position and warp, which Wayland leaves
// to the shell alone. io.github.sidecontrol.Shell at /io/github/sidecontrol/Shell answers the
// backend (side_control/backend/compositor.py, Mutter): GetCursor is where the pointer is, Warp
// moves it. GNOME 45 and later (ES modules); installed from Side Control's settings.

import Gio from 'gi://Gio';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const NAME = 'io.github.sidecontrol.Shell';
const PATH = '/io/github/sidecontrol/Shell';

const INTERFACE = `<node>
  <interface name="${NAME}">
    <method name="GetCursor">
      <arg name="x" type="i" direction="out"/>
      <arg name="y" type="i" direction="out"/>
    </method>
    <method name="Warp">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
  </interface>
</node>`;

export default class SideControlExtension extends Extension {
    enable() {
        this._dbus = Gio.DBusExportedObject.wrapJSObject(INTERFACE, this);
        this._dbus.export(Gio.DBus.session, PATH);
        this._owner = Gio.bus_own_name(Gio.BusType.SESSION, NAME,
            Gio.BusNameOwnerFlags.NONE, null, null, null);
    }

    disable() {
        Gio.bus_unown_name(this._owner);
        this._dbus.unexport();
        this._dbus = null;
    }

    GetCursor() {
        const [x, y] = global.get_pointer();
        return [x, y];
    }

    Warp(x, y) {
        // Clutter.get_default_backend() is gone from mutter's Clutter, so the seat comes from the
        // stage's context, the way GNOME Shell itself does it (js/ui/magnifier.js). The warp works
        // on Wayland: the native seat injects absolute motion (meta-seat-native.c -> seat impl).
        global.stage.get_context().get_backend().get_default_seat().warp_pointer(x, y);
    }
}
