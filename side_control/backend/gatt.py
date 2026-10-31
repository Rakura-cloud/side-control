# SPDX-License-Identifier: GPL-3.0-or-later
"""Reading a device's GATT characteristics (model, OS, battery) over BR/EDR."""

import socket
import struct
import time


ATT_REQUESTS = {0x02, 0x04, 0x06, 0x08, 0x0A, 0x0C, 0x0E, 0x10, 0x12, 0x16, 0x18, 0x20}


GATT_MODEL, GATT_MANUFACTURER, GATT_SOFTWARE, GATT_BATTERY = 0x2A24, 0x2A29, 0x2A28, 0x2A19


def read_gatt(addr, uuids):
    """{uuid: value bytes} of the characteristics found among `uuids` (Model Number String "iPhone16,1",
    Battery Level, ...), read over ATT on BR/EDR (L2CAP PSM 31) - no LE connection or pairing change."""
    found = {}
    try:
        with socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP) as sock:
            sock.settimeout(5)
            sock.connect((addr, 31))
            for uuid in uuids:
                sock.send(struct.pack("<BHHH", 0x08, 1, 0xFFFF, uuid))  # Read By Type
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    pkt = sock.recv(512)
                    op = pkt[0]
                    if op == 0x09:  # Read By Type response: len, then handle(2) + value
                        found[uuid] = pkt[4:2 + pkt[1]]
                        break
                    if op == 0x01 and pkt[1] == 0x08:  # our request failed: not there
                        break
                    if op == 0x1D:  # the phone's own indications (Service Changed) want a confirmation
                        sock.send(b"\x1e")
                    elif op in ATT_REQUESTS:  # ... and its requests an answer, even if "not supported"
                        sock.send(struct.pack("<BBHB", 0x01, op, 0, 0x06))
    except (OSError, IndexError):
        pass
    return found


def read_phone_model(addr):
    value = read_gatt(addr, [GATT_MODEL]).get(GATT_MODEL)
    return value.decode(errors="replace") if value else None
