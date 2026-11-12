import importlib.machinery
import importlib.util
import io
import json
import os
import struct
import sys
import tempfile
import unittest
import zlib
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
_loader = importlib.machinery.SourceFileLoader("cu", os.path.join(HERE, "..", "bin", "cu"))
_spec = importlib.util.spec_from_loader("cu", _loader)
cu = importlib.util.module_from_spec(_spec)
_loader.exec_module(cu)


def make_xwd(width, height, pixels, bpp=32, byte_order=0, masks=(0xFF0000, 0xFF00, 0xFF), pad=0):
    bypp = bpp // 8
    bpl = width * bypp + pad
    name = b"root\x00"
    header_size = 100 + len(name)
    fields = [header_size, 7, 2, 24, width, height, 0, byte_order, 32, 0, 32, bpp, bpl, 4,
              masks[0], masks[1], masks[2], 8, 256, 0, width, height, 0, 0, 0]
    data = bytearray(struct.pack(">25I", *fields) + name)
    endian = "little" if byte_order == 0 else "big"
    for y in range(height):
        for x in range(width):
            r, g, b = pixels[y][x]
            v = (r << 16) | (g << 8) | b
            data += v.to_bytes(bypp, endian)
        data += b"\x00" * pad
    return bytes(data)


def decode_png(png):
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, w, h = 8, b"", 0, 0
    while pos < len(png):
        (n,) = struct.unpack(">I", png[pos:pos + 4])
        kind = png[pos + 4:pos + 8]
        body = png[pos + 8:pos + 8 + n]
        (crc,) = struct.unpack(">I", png[pos + 8 + n:pos + 12 + n])
        assert crc == zlib.crc32(kind + body) & 0xFFFFFFFF
        if kind == b"IHDR":
            w, h = struct.unpack(">II", body[:8])
        elif kind == b"IDAT":
            idat += body
        pos += 12 + n
    raw = zlib.decompress(idat)
    rows = []
    stride = w * 3 + 1
    for y in range(h):
        row = raw[y * stride:(y + 1) * stride]
        assert row[0] == 0
        rows.append([tuple(row[1 + x * 3:4 + x * 3]) for x in range(w)])
    return w, h, rows


PIX = [[(255, 0, 0), (0, 255, 0), (0, 0, 255)], [(1, 2, 3), (250, 128, 7), (0, 0, 0)]]


class XwdTests(unittest.TestCase):
    def roundtrip(self, **kw):
        w, h, rows = cu.xwd_to_rgb(make_xwd(3, 2, PIX, **kw))
        self.assertEqual((w, h), (3, 2))
        self.assertEqual(decode_png(cu.png_bytes(w, h, rows))[2], PIX)

    def test_lsb_32(self):
        self.roundtrip()

    def test_msb_32(self):
        self.roundtrip(byte_order=1)

    def test_row_padding(self):
        self.roundtrip(pad=4)

    def test_24bpp(self):
        self.roundtrip(bpp=24)

    def test_rgb565_generic_path(self):
        pix = [[(255, 0, 0), (0, 255, 0)], [(0, 0, 255), (255, 255, 255)]]
        width, height = 2, 2
        fields = [100, 7, 2, 16, width, height, 0, 0, 16, 0, 16, 16, 4, 4,
                  0xF800, 0x07E0, 0x001F, 6, 0, 0, width, height, 0, 0, 0]
        data = bytearray(struct.pack(">25I", *fields))
        for row in pix:
            for r, g, b in row:
                data += (((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)).to_bytes(2, "little")
        _, _, rows = cu.xwd_to_rgb(bytes(data))
        self.assertEqual(decode_png(cu.png_bytes(2, 2, rows))[2], pix)

    def test_truncated(self):
        with self.assertRaises(cu.CuError):
            cu.xwd_to_rgb(make_xwd(3, 2, PIX)[:-5])


class XpropTests(unittest.TestCase):
    def test_active_window_with_trailing_zero(self):
        self.assertEqual(cu.parse_active_window("_NET_ACTIVE_WINDOW(WINDOW): window id # 0x2c00004, 0x0"), 0x2C00004)

    def test_active_window_plain(self):
        self.assertEqual(cu.parse_active_window("_NET_ACTIVE_WINDOW(WINDOW): window id # 0x1a00003"), 0x1A00003)

    def test_no_active_window(self):
        self.assertIsNone(cu.parse_active_window("_NET_ACTIVE_WINDOW(WINDOW): window id # 0x0"))
        self.assertIsNone(cu.parse_active_window("_NET_ACTIVE_WINDOW:  not found."))

    def test_props(self):
        out = ('_NET_WM_NAME(UTF8_STRING) = "Say \\"hi\\" — Firefox"\n'
               'WM_NAME(STRING) = "ignored"\n'
               'WM_CLASS(STRING) = "Navigator", "firefox"\n'
               '_NET_WM_PID(CARDINAL) = 4242\n')
        self.assertEqual(cu.parse_window_props(out),
                         {"title": 'Say "hi" — Firefox', "instance": "Navigator", "class": "firefox", "pid": 4242})


class ValidationTests(unittest.TestCase):
    def bad(self, item):
        with self.assertRaises(cu.CuError) as cm:
            cu.validate_action(item)
        self.assertEqual(cm.exception.exit_code, cu.EXIT_USAGE)

    def test_valid_defaults(self):
        self.assertEqual(cu.validate_action({"action": "click", "x": 1, "y": 2}),
                         {"action": "click", "x": 1, "y": 2, "button": "left", "count": 1})
        self.assertEqual(cu.validate_action({"action": "scroll", "direction": "down"})["amount"], 3)

    def test_invalid(self):
        self.bad({"action": "wait", "seconds": -1})
        self.bad({"action": "wait", "seconds": 61})
        self.bad({"action": "wait", "seconds": True})
        self.bad({"action": "scroll", "direction": "down", "amount": 0})
        self.bad({"action": "scroll", "direction": "sideways"})
        self.bad({"action": "click", "x": "1", "y": 2})
        self.bad({"action": "click", "x": 1, "y": 2, "button": "fourth"})
        self.bad({"action": "click", "x": 1, "y": 2, "count": 0})
        self.bad({"action": "type", "text": ""})
        self.bad({"action": "key", "keys": "ctrl+"})
        self.bad({"action": "open-tab", "url": "a\nb"})
        self.bad({"action": "teleport"})
        self.bad({"action": "info", "extra": 1})
        self.bad(["not", "an", "object"])
        self.bad({"action": []})
        self.bad({"action": "scroll", "direction": []})
        self.bad({"action": "click", "x": 1, "y": 2, "button": {}})

    def test_default_screenshot_paths_are_unique(self):
        a, b = cu.default_screenshot_path(), cu.default_screenshot_path()
        try:
            self.assertNotEqual(a, b)
        finally:
            for f in (a, b):
                os.unlink(f)

    def test_chords(self):
        self.assertEqual(cu.parse_chord("ctrl+shift+Tab"), ["ctrl", "shift", "Tab"])
        self.assertEqual(cu.parse_chord("ctrl++"), ["ctrl", "+"])
        self.assertEqual(cu.parse_chord("+"), ["+"])

    def test_char_keysyms(self):
        self.assertEqual(cu.char_keysym("a"), 0x61)
        self.assertEqual(cu.char_keysym("é"), 0xE9)
        self.assertEqual(cu.char_keysym("€"), 0x010020AC)
        self.assertEqual(cu.char_keysym("\n"), 0xFF0D)


class CliTests(unittest.TestCase):
    def run_cli(self, argv, stdin=None):
        buf = io.StringIO()
        old = sys.stdin
        if stdin is not None:
            sys.stdin = io.StringIO(stdin)
        try:
            with redirect_stdout(buf):
                code = cu.main(argv)
        finally:
            sys.stdin = old
        return code, json.loads(buf.getvalue())

    def test_wait(self):
        code, out = self.run_cli(["wait", "0"])
        self.assertEqual((code, out["ok"], out["action"]), (0, True, "wait"))

    def test_negative_wait(self):
        code, out = self.run_cli(["wait", "-1"])
        self.assertEqual(code, cu.EXIT_USAGE)
        self.assertFalse(out["ok"])

    def test_bad_args_are_json(self):
        code, out = self.run_cli(["click", "abc"])
        self.assertEqual(code, cu.EXIT_USAGE)
        self.assertEqual(out["error"]["code"], "invalid_argument")

    def test_malformed_batch_item_reports_index_and_runs_nothing(self):
        batch = json.dumps([{"action": "wait", "seconds": 0}, {"action": "scroll", "direction": "down", "amount": 0}])
        code, out = self.run_cli(["batch", "-"], stdin=batch)
        self.assertEqual(code, cu.EXIT_USAGE)
        self.assertEqual(out["error"]["index"], 1)

    def test_batch_not_json(self):
        code, out = self.run_cli(["batch", "-"], stdin="{nope")
        self.assertEqual(code, cu.EXIT_USAGE)

    def test_batch_ordered_results(self):
        batch = json.dumps([{"action": "wait", "seconds": 0}, {"action": "wait", "seconds": 0.01}])
        code, out = self.run_cli(["batch", "-"], stdin=batch)
        self.assertEqual(code, 0)
        self.assertEqual([r["seconds"] for r in out["results"]], [0.0, 0.01])

    def test_missing_display(self):
        old = os.environ.get("DISPLAY")
        os.environ["DISPLAY"] = ":987"
        try:
            code, out = self.run_cli(["info"])
        finally:
            if old is None:
                del os.environ["DISPLAY"]
            else:
                os.environ["DISPLAY"] = old
        self.assertEqual(code, cu.EXIT_RUNTIME)
        self.assertEqual(out["error"]["code"], "display_unavailable")
        self.assertIn("hint", out["error"])

    def test_batch_runtime_failure_keeps_earlier_results(self):
        with tempfile.TemporaryDirectory() as d:
            batch = json.dumps([{"action": "wait", "seconds": 0},
                                {"action": "screenshot", "path": os.path.join(d, "missing", "x.png")}])
            code, out = self.run_cli(["batch", "-"], stdin=batch)
        self.assertEqual(code, cu.EXIT_RUNTIME)
        self.assertEqual((out["completed"], out["failed_index"]), (1, 1))

    @unittest.skipUnless(os.environ.get("DISPLAY"), "needs an X display (no input is sent)")
    def test_unknown_key_in_batch_fails_before_anything_runs(self):
        batch = json.dumps([{"action": "wait", "seconds": 0}, {"action": "key", "keys": "ctrl+bogus"}])
        code, out = self.run_cli(["batch", "-"], stdin=batch)
        self.assertEqual(code, cu.EXIT_USAGE)
        self.assertEqual((out["completed"], out["failed_index"]), (0, 1))


class ButtonReleaseTests(unittest.TestCase):
    def test_button_released_when_interrupted(self):
        events = []

        class FakeTst:
            def XTestFakeButtonEvent(self, dpy, n, press, delay):
                events.append((n, press))

        class FakeX11:
            def XFlush(self, dpy):
                raise KeyboardInterrupt

            def XSync(self, dpy, discard):
                pass

        xd = cu.XDisplay.__new__(cu.XDisplay)
        xd.dpy, xd.xtst, xd.x11, xd._held, xd._buttons_held = 1, FakeTst(), FakeX11(), [], []
        with self.assertRaises(KeyboardInterrupt):
            xd.button(1)
        self.assertEqual(events, [(1, 1), (1, 0)])

    def test_button_released_when_press_call_interrupted(self):
        events = []

        class FakeTst:
            def XTestFakeButtonEvent(self, dpy, n, press, delay):
                events.append((n, press))
                if press and len(events) == 1:
                    raise KeyboardInterrupt

        class FakeX11:
            def XFlush(self, dpy):
                pass

            def XSync(self, dpy, discard):
                pass

        xd = cu.XDisplay.__new__(cu.XDisplay)
        xd.dpy, xd.xtst, xd.x11, xd._held, xd._buttons_held = 1, FakeTst(), FakeX11(), [], []
        with self.assertRaises(KeyboardInterrupt):
            xd.button(3)
        self.assertEqual(events, [(3, 1), (3, 0)])


class ScreenshotPathTests(unittest.TestCase):
    def test_mkstemp_failure_is_structured(self):
        from unittest import mock
        with mock.patch.object(cu.tempfile, "mkstemp", side_effect=OSError(28, "No space left on device")):
            with self.assertRaises(cu.CuError) as cm:
                cu.default_screenshot_path()
        self.assertEqual(cm.exception.code, "bad_path")

    def test_no_usable_temp_dir_is_structured(self):
        from unittest import mock
        with mock.patch.object(cu.tempfile, "gettempdir", side_effect=FileNotFoundError(2, "No usable temporary directory")), \
                mock.patch.object(cu.tempfile, "mkstemp", side_effect=FileNotFoundError(2, "No usable temporary directory")):
            with self.assertRaises(cu.CuError) as cm:
                cu.default_screenshot_path()
        self.assertEqual(cm.exception.code, "bad_path")


if __name__ == "__main__":
    unittest.main()
