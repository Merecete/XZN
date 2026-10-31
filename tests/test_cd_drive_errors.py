"""CD drive: reconnect after a lost connection, scratches, fresh handle for SCAN."""
import os, sys, tempfile
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import xzn, T
ok = True
def check(c, m):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + m)

class Flaky(xzn.WinCdDrive):
    def __init__(self, fail_mode):
        xzn._RawCdDrive.__init__(self); self.id = "G:"; self.label = "G:"
        self._h = None; self.opens = 0; self.closes = 0; self.mode = fail_mode; self.reads = 0
    def _open(self):
        if self._h is None:
            self.opens += 1; self._h = self.opens
    def _close(self):
        if self._h is not None:
            self.closes += 1; self._h = None
    def _read_raw(self, lsn, n):
        self.reads += 1
        if self.mode == "remount" and self._h == 1 and lsn >= 48:
            raise OSError(55, "DeviceIoControl failed")      # old handle became invalid
        if self.mode == "gone":
            raise OSError(55, "DeviceIoControl failed")
        if self.mode == "scratch" and lsn == 24:
            raise OSError(23, "CRC error")
        return bytes([lsn % 251]) * (n * 2352)

with mock.patch.object(xzn.time, "sleep"):
    d = Flaky("remount")
    data = d.read_sectors(0, 96)
    check(len(data) == 96 * 2352 and data[48 * 2352] == 48 and d.opens == 2,
          f"after Windows re-mount (error 55) the drive is reopened and reading continues (opens={d.opens})")
    d = Flaky("gone")
    try:
        d.read_sectors(0, 24); res = "no error"
    except RuntimeError as e:
        res = str(e)
    check("not responding" in res and d.opens == 7, f"drive really gone -> clear error, no endless silence ({res})")
    d = Flaky("scratch")
    data = d.read_sectors(0, 72)
    check(data[24 * 2352:48 * 2352] == bytes(24 * 2352) and data[48 * 2352] == 48 and d.opens == 1,
          "scratched block -> 3 tries, then silence, drive not reopened")
    # TOC always uses a fresh handle
    w = Flaky("ok"); w._h = 99
    with mock.patch.object(xzn.WinCdDrive, "_ioctl", return_value=b"\x00\x00\x01\x01" + bytes([0, 0x10, 1, 0, 0, 0, 2, 0]) + bytes([0, 0x10, 0xAA, 0, 0, 3, 0, 0]) + bytes(800)), \
         mock.patch.object(xzn.WinCdDrive, "_k32"):
        t = xzn.WinCdDrive.read_toc(w)
    check(w.closes == 1 and t["offsets"] == {1: 0}, "SCAN reads the TOC with a fresh handle (fixes error 1006)")
print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
