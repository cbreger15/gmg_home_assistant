"""A grill on the other end of the UDP link, for tests.

Real replies from a Jim Bowie (firmware 2.3, "NJB APIv6"), taken from Home
Assistant's history of the raw status sensor between 13 and 16 Sep 2026, and
WireGrill, which answers Grill.send with them. Plain Python: no Home Assistant
and no network needed -- and NoNetwork makes sure of the second part.
"""

import socket as _socket
import threading

# Test addresses are from TEST-NET-1 (RFC 5737), which is never routed.
TEST_IP = "192.0.2.94"


class NoNetwork:
    """Stands in for the `socket` module inside gmg.py during tests.

    Everything passes through except opening a socket, which fails the test.
    Tests must reach the grill through WireGrill; if a change ever routes a
    send around it, that must be a failing test, not a UDP frame on the LAN.
    """

    def __getattr__(self, name):
        return getattr(_socket, name)

    @staticmethod
    def socket(*args, **kwargs):
        raise AssertionError("a test tried to open a real socket -- simulate the grill instead")

STATUS = b"UR001!"
LIVE_REPLY = object()  # in a WireGrill script: answer with the current packet

# Whole 52-byte replies, keyed by byte 9 as the GMG app left it -- one setting
# changed per Confirm on 16 Sep 2026. The grill was idle for all of them: off,
# nothing in either probe jack (601), and the "JB02SUF02.3" model string in
# bytes 41-51.
LIVE = {
    "03": bytes.fromhex(  # 16 Sep 12:03 UTC
        "5552400059029600060314321919191959020000ffffffff00000000000000000100000300000000"
        "004a42303253554630322e33"
    ),
    "07": bytes.fromhex(  # 15 Sep 15:44 UTC
        "5552480059029600060714321919191959020000ffffffff00000000000000000100000300000000"
        "004a42303253554630322e33"
    ),
    "09": bytes.fromhex(  # 16 Sep 12:16 UTC -- the block as it stands now
        "55524b0059029600060914321919191959020000ffffffff00000000000000000100000300000000"
        "004a42303253554630322e33"
    ),
    "0a": bytes.fromhex(  # 16 Sep 12:05 UTC
        "5552410059029600060a14321919191959020000ffffffff00000000000000000100000300000000"
        "004a42303253554630322e33"
    ),
    "0b": bytes.fromhex(  # 13 Sep 13:27 UTC
        "55523e0059029600060b14321919191959020000ffffffff00000000000000000100000300000000"
        "004a42303253554630322e33"
    ),
    "0f": bytes.fromhex(  # 16 Sep 12:01 UTC
        "5552400059029600060f14321919191959020000ffffffff00000000000000000100000300000000"
        "004a42303253554630322e33"
    ),
    "13": bytes.fromhex(  # 16 Sep 12:02 UTC
        "5552410059029600061314321919191959020000ffffffff00000000000000000100000300000000"
        "004a42303253554630322e33"
    ),
    "27": bytes.fromhex(  # 16 Sep 11:54 UTC
        "5552410059029600062714321919191959020000ffffffff00000000000000000100000300000000"
        "004a42303253554630322e33"
    ),
}

# 16 Sep 15:07 UTC, after the GMG app set the three left calibration boxes to
# +2 (grill, 150F), +8 (probe 1, 32F) and +4 (probe 2, 32F), right boxes 0.
# Byte 12 is 0x21 -- the "!" that ends every command -- and the grill took the
# app's write whole. The empty probe jacks read 584 and 593 instead of 601:
# the grill applies its calibration to that reading too.
APP_CALIBRATED = bytes.fromhex(
    "55525000480296000609163221191d1951020000ffffffff00000000000000000100000300000000"
    "004a42303253554630322e33"
)

# Real pieces of a reply (13 Sep): one byte short of whole, and two whole
# replies run together. Both start UR, so their status fields are readable,
# but neither is exactly one packet -- and both carry an older block (0b).
TAIL_CUT_51 = bytes.fromhex(
    "55523e0059029600060b14321919191959020000ffffffff00000000000000000100000300000000"
    "004a42303253554630322e"
)
MERGED_104 = LIVE["0b"] + LIVE["0b"]


class WireGrill:
    """The grill, as seen through Grill.send.

    UR001! is answered with the current 52-byte packet -- or, while `script`
    lasts, with the next scripted reply (None for a lost one, a cut or merged
    one, or LIVE_REPLY). A UC frame replaces bytes 8-15 the way the app's
    writes do: `apply` may change what lands (or return None to ignore the
    frame), and `applies_after` delays it by that many status polls.
    `rendezvous` holds each status poll until another thread polls too.
    Anything else (UN!, UK..., UT...) gets no reply.
    """

    def __init__(self, packet, script=(), apply=None, applies_after=0, rendezvous=None):
        self.packet = bytearray(packet)
        self.script = list(script)
        self.apply = apply or (lambda block: block)
        self.applies_after = applies_after
        self.rendezvous = rendezvous
        self.pending = None
        self.countdown = 0
        self.events = []
        self._lock = threading.Lock()

    def send(self, message, timeout=1):
        if message == STATUS and self.rendezvous is not None:
            try:
                self.rendezvous.wait()
            except threading.BrokenBarrierError:
                pass
        with self._lock:
            self.events.append(("send", message))
            if message == STATUS:
                if self.pending is not None:
                    self.countdown -= 1
                    if self.countdown <= 0:
                        self.packet[8:16] = self.pending
                        self.pending = None
                reply = self.script.pop(0) if self.script else LIVE_REPLY
                return bytes(self.packet) if reply is LIVE_REPLY else reply
            if len(message) == 11 and message[:2] == b"UC" and message[-1:] == b"!":
                landed = self.apply(message[2:10])
                if landed is not None:
                    if self.applies_after:
                        self.pending, self.countdown = landed, self.applies_after
                    else:
                        self.packet[8:16] = landed
            return None

    def sleep(self, seconds):
        self.events.append(("sleep", seconds))

    def sent(self):
        return [message for kind, message in self.events if kind == "send"]

    def writes(self):
        return [message for message in self.sent() if message[:2] == b"UC"]
