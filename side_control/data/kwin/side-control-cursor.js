// SPDX-License-Identifier: GPL-3.0-or-later
// Side Control's KWin script: says where the PC pointer is. Wayland gives clients no pointer
// position and KWin only shows workspace.cursorPos to scripts, so this reports every change over
// D-Bus to the backend's io.github.sidecontrol.Cursor service (side_control/backend/compositor.py,
// KWin). It cannot move the pointer: workspace.cursorPos is read-only and KWin has no API that
// moves it (see the KWin docstring there).
//
// Loaded and unloaded over D-Bus by the backend: org.kde.kwin.Scripting.loadScript / unloadScript
// on org.kde.KWin /Scripting. workspace.cursorPos and its cursorPosChanged() signal are named the
// same in KWin 5 and 6 (scripting/workspace_wrapper.h).

var SERVICE = "io.github.sidecontrol.Cursor";
var PATH = "/io/github/sidecontrol/Cursor";
var INTERFACE = "io.github.sidecontrol.Cursor";
var EVERY_MS = 50; // at most one report every 50 ms: cursorPosChanged fires on every motion event

var sent = Date.now();
var timer = new QTimer(); // trailing, so the last position before the pointer stops is sent too

function report() {
    timer.stop();
    sent = Date.now();
    var pos = workspace.cursorPos;
    // callDBus marshals JS numbers as int or double depending on the engine; strings arrive the
    // same ("ss") on every KWin
    callDBus(SERVICE, PATH, INTERFACE, "Report", String(pos.x), String(pos.y));
}

function changed() {
    var wait = EVERY_MS - (Date.now() - sent);
    if (wait <= 0) {
        report();
    } else if (!timer.active) {
        timer.start(wait);
    }
}

timer.timeout.connect(report);
workspace.cursorPosChanged.connect(changed);
report(); // where the pointer is before anything has moved
