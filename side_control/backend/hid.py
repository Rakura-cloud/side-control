# SPDX-License-Identifier: GPL-3.0-or-later
"""The Bluetooth HID keyboard + mouse: report descriptor, SDP record, key tables, L2CAP channels."""

import fcntl
import socket
import struct

from evdev import ecodes as e

from .config import NAME
from .events import log


HID_UUID = "00001124-0000-1000-8000-00805f9b34fb"
PSM_CTRL, PSM_INTR = 0x11, 0x13
SOL_BLUETOOTH, BT_SECURITY, BT_SECURITY_MEDIUM = 274, 4, 2
TIOCOUTQ = 0x5411
HCIGETCONNINFO = 0x800448D5
BACKLOG_BYTES = 4096  # queued bytes (incl. kernel overhead) before motion is held back

REPORT_MAP = bytes.fromhex(
    # keyboard, report id 1: modifiers, reserved, 6 keys (+ LED output)
    "05 01 09 06 a1 01 85 01 05 07 19 e0 29 e7 15 00 25 01 75 01 95 08 81 02"
    "95 01 75 08 81 01 95 05 75 01 05 08 19 01 29 05 91 02 95 01 75 03 91 01"
    "95 06 75 08 15 00 25 65 05 07 19 00 29 65 81 00 c0"
    # mouse, report id 2: 8 buttons, x, y, wheel, AC Pan (horizontal scroll)
    "05 01 09 02 a1 01 85 02 09 01 a1 00 05 09 19 01 29 08 15 00 25 01 95 08"
    "75 01 81 02 05 01 09 30 09 31 09 38 15 81 25 7f 75 08"
    "95 03 81 06 05 0c 0a 38 02 95 01 81 06 c0 c0"
)

SDP_RECORD = f"""<?xml version="1.0" encoding="UTF-8" ?>
<record>
  <attribute id="0x0001"><sequence><uuid value="0x1124" /></sequence></attribute>
  <attribute id="0x0004"><sequence>
    <sequence><uuid value="0x0100" /><uint16 value="0x0011" /></sequence>
    <sequence><uuid value="0x0011" /></sequence>
  </sequence></attribute>
  <attribute id="0x0005"><sequence><uuid value="0x1002" /></sequence></attribute>
  <attribute id="0x0006"><sequence>
    <uint16 value="0x656e" /><uint16 value="0x006a" /><uint16 value="0x0100" />
  </sequence></attribute>
  <attribute id="0x0009"><sequence>
    <sequence><uuid value="0x1124" /><uint16 value="0x0101" /></sequence>
  </sequence></attribute>
  <attribute id="0x000d"><sequence><sequence>
    <sequence><uuid value="0x0100" /><uint16 value="0x0013" /></sequence>
    <sequence><uuid value="0x0011" /></sequence>
  </sequence></sequence></attribute>
  <attribute id="0x0100"><text value="{NAME}" /></attribute>
  <attribute id="0x0101"><text value="Keyboard and Mouse" /></attribute>
  <attribute id="0x0102"><text value="Linux" /></attribute>
  <attribute id="0x0201"><uint16 value="0x0111" /></attribute>
  <attribute id="0x0202"><uint8 value="0xc0" /></attribute>
  <attribute id="0x0203"><uint8 value="0x00" /></attribute>
  <attribute id="0x0204"><boolean value="true" /></attribute>
  <attribute id="0x0205"><boolean value="true" /></attribute>
  <attribute id="0x0206"><sequence><sequence>
    <uint8 value="0x22" /><text encoding="hex" value="{REPORT_MAP.hex()}" />
  </sequence></sequence></attribute>
  <attribute id="0x0207"><sequence><sequence>
    <uint16 value="0x0409" /><uint16 value="0x0100" />
  </sequence></sequence></attribute>
  <attribute id="0x020b"><uint16 value="0x0100" /></attribute>
  <attribute id="0x020c"><uint16 value="0x0c80" /></attribute>
  <attribute id="0x020d"><boolean value="true" /></attribute>
  <attribute id="0x020e"><boolean value="false" /></attribute>
  <attribute id="0x020f"><uint16 value="0x0010" /></attribute>
  <attribute id="0x0210"><uint16 value="0x0320" /></attribute>
</record>
"""

# evdev keycode -> HID usage
KEYMAP = {getattr(e, "KEY_" + c): 4 + i for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ")}
KEYMAP.update({getattr(e, "KEY_" + c): 30 + i for i, c in enumerate("1234567890")})
KEYMAP.update({getattr(e, f"KEY_F{i + 1}"): 58 + i for i in range(12)})
KEYMAP.update({
    e.KEY_ENTER: 40, e.KEY_ESC: 41, e.KEY_BACKSPACE: 42, e.KEY_TAB: 43, e.KEY_SPACE: 44,
    e.KEY_MINUS: 45, e.KEY_EQUAL: 46, e.KEY_LEFTBRACE: 47, e.KEY_RIGHTBRACE: 48,
    e.KEY_BACKSLASH: 49, e.KEY_SEMICOLON: 51, e.KEY_APOSTROPHE: 52, e.KEY_GRAVE: 53,
    e.KEY_COMMA: 54, e.KEY_DOT: 55, e.KEY_SLASH: 56, e.KEY_CAPSLOCK: 57,
    e.KEY_SYSRQ: 70, e.KEY_SCROLLLOCK: 71, e.KEY_PAUSE: 72, e.KEY_INSERT: 73,
    e.KEY_HOME: 74, e.KEY_PAGEUP: 75, e.KEY_DELETE: 76, e.KEY_END: 77, e.KEY_PAGEDOWN: 78,
    e.KEY_RIGHT: 79, e.KEY_LEFT: 80, e.KEY_DOWN: 81, e.KEY_UP: 82, e.KEY_102ND: 100,
    e.KEY_KPENTER: 88,
})
MODS = {
    e.KEY_LEFTCTRL: 0x01, e.KEY_LEFTSHIFT: 0x02, e.KEY_LEFTALT: 0x04, e.KEY_LEFTMETA: 0x08,
    e.KEY_RIGHTCTRL: 0x10, e.KEY_RIGHTSHIFT: 0x20, e.KEY_RIGHTALT: 0x40, e.KEY_RIGHTMETA: 0x80,
}
BUTTONS = {e.BTN_LEFT: 1, e.BTN_RIGHT: 2, e.BTN_MIDDLE: 4}
FINGERS = {e.BTN_TOOL_FINGER: 1, e.BTN_TOOL_DOUBLETAP: 2, e.BTN_TOOL_TRIPLETAP: 3,
           e.BTN_TOOL_QUADTAP: 4}


def listen(addr, psm):
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(SOL_BLUETOOTH, BT_SECURITY, struct.pack("BB", BT_SECURITY_MEDIUM, 0))
    s.bind((addr, psm))
    s.listen(4)
    return s


def dial(peer, psm):
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
    s.setsockopt(SOL_BLUETOOTH, BT_SECURITY, struct.pack("BB", BT_SECURITY_MEDIUM, 0))
    s.settimeout(5)
    try:
        s.connect((peer, psm))
    except OSError:
        s.close()
        raise
    s.settimeout(None)
    return s


def disable_sniff(dev_id, peer, quiet=False):
    """Stop the link to `peer` from entering sniff (power-save) mode, which delays every report."""
    hci = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_RAW, socket.BTPROTO_HCI)
    try:
        hci.bind((dev_id,))
        # struct hci_conn_info_req (bdaddr, type=ACL, pad) followed by struct hci_conn_info
        req = bytearray(bytes.fromhex(peer.replace(":", ""))[::-1] + b"\x01" + bytes(17))
        fcntl.ioctl(hci, HCIGETCONNINFO, req)
        handle = struct.unpack_from("<H", req, 8)[0]

        def command(ocf, params):  # OGF 0x02: link policy commands
            hci.send(b"\x01" + struct.pack("<HB", (0x02 << 10) | ocf, len(params)) + params)

        command(0x000D, struct.pack("<HH", handle, 0x0001))  # policy: role switch only, no sniff
        command(0x0004, struct.pack("<H", handle))  # exit sniff if already in it
        if not quiet:
            log("power-save (sniff) disabled for this link")
    except OSError as err:
        log(f"could not disable sniff mode: {err}")
    finally:
        hci.close()
