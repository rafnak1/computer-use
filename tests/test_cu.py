import importlib.machinery
import importlib.util
import io
import json
import os
import struct
import sys
import tempfile
import unittest
import unittest.mock
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
                         {"action": "click", "x": 1, "y": 2, "button": "left", "count": 1, "hover": 0.15})
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
        self.bad({"action": "click", "x": 1, "y": 2, "hover": -0.1})
        self.bad({"action": "click", "x": 1, "y": 2, "hover": 5.01})
        self.bad({"action": "click", "x": 1, "y": 2, "hover": float("nan")})
        self.bad({"action": "click", "x": 1, "y": 2, "hover": True})
        self.bad({"action": "click", "x": 1, "y": 2, "hover": "0.2"})

    def test_click_hover_values(self):
        def hover(v):
            return cu.validate_action({"action": "click", "x": 1, "y": 2, "hover": v})["hover"]
        self.assertEqual(hover(0), 0.0)
        self.assertIsInstance(hover(0), float)
        self.assertEqual(hover(5), 5.0)
        self.assertEqual(hover(0.3), 0.3)
        self.assertEqual(hover(None), cu.DEFAULT_HOVER)

    def test_cli_click_hover_is_validated(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cu.main(["click", "1", "2", "--hover", "9"])
        self.assertEqual(code, cu.EXIT_USAGE)
        self.assertIn("hover", json.loads(buf.getvalue())["error"]["message"])

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


class FakeKeymapDisplay:
    """In-memory stand-in for the XDisplay keymap methods used by type_text."""

    def __init__(self, rows, direct, mask=0, modifiers=(), pressed=()):
        self.rows = dict(rows)
        # Simulated (possibly stale) client-side XKB cache: (keycode, level) -> keysym
        self.xkb = {(kc, int(shift)): ks for ks, (kc, shift) in direct.items()}
        xkb = self.xkb

        class X11:
            @staticmethod
            def XkbKeycodeToKeysym(_dpy, kc, _group, level):
                return xkb.get((kc, level), 0)
        self.x11 = X11
        self.dpy = None
        self.mask = mask
        self.mods = set(modifiers)
        self.pressed = set(pressed)
        self._errors = []
        self.log = []

    # keymap API
    def keyboard_mapping(self, first=None, count=None):
        kcs = sorted(self.rows)
        first = kcs[0] if first is None else first
        count = kcs[-1] - first + 1 if count is None else count
        return kcs[0], kcs[-1], 2, {k: self.rows[k] for k in range(first, first + count) if k in self.rows}

    def keycode_row(self, kc):
        return self.rows[kc]

    def set_keycode_row(self, kc, row):
        self.log.append(("map", kc, tuple(row)))
        syms = [ks for ks in row if ks]
        if syms and len(set(syms)) == 1:
            row = (syms[0], 0, syms[0])  # like XKB: a one-level key reads back rewritten
        self.rows[kc] = tuple(row)

    def sync(self):
        pass

    def modifier_keycodes(self):
        return set(self.mods)

    def pressed_keycodes(self):
        return set(self.pressed)

    def modifier_mask(self):
        return self.mask

    direct_keysyms = cu.XDisplay.direct_keysyms

    def keycode_levels(self, ks):
        return (50, False) if ks == cu.SHIFT_KEYSYM else (0, False)

    def tap(self, codes):
        self.log.append(("tap", tuple(codes), tuple(self.rows.get(c) for c in codes)))

    type_text = cu.XDisplay.type_text


class KeymapTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = unittest.mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.tmp.name, "DISPLAY": ":99"})
        self.env.start()
        self.settle = unittest.mock.patch.object(cu, "KEYMAP_SETTLE", 0)
        self.settle.start()

    def tearDown(self):
        self.settle.stop()
        self.env.stop()
        self.tmp.cleanup()

    @staticmethod
    def layout(empty=(200, 201)):
        rows = {kc: (0x61 + kc - 10, 0x41 + kc - 10) for kc in range(10, 36)}  # fake letters
        rows[65] = (0x20, 0x20)
        for kc in empty:
            rows[kc] = (0, 0)
        direct = {row[0]: (kc, False) for kc, row in rows.items() if row[0]}
        direct.update({row[1]: (kc, True) for kc, row in rows.items() if row[1]})
        return rows, direct


class TypingPlanTests(unittest.TestCase):
    def test_text_keysyms(self):
        self.assertEqual(cu.text_keysyms("a\r\né😀"), [0x61, 0xFF0D, 0xE9, 0x0101F600])
        for bad in ("a\x07", "\x85", "\udc80"):
            with self.assertRaises(cu.CuError):
                cu.text_keysyms(bad)

    def test_blocking_state(self):
        self.assertEqual(cu.blocking_state(0x10), [])  # NumLock is fine
        self.assertEqual(cu.blocking_state(0x02 | 0x2000), ["Caps Lock", "keyboard group 2"])

    def test_find_scratch_keycodes(self):
        rows = {7: (0,), 8: (0, 0), 9: (0x61,), 10: (0, 0x41), 11: (), 12: (0,)}
        self.assertEqual(cu.find_scratch_keycodes(rows, exclude={12}), [11, 8])

    def test_plan_reuses_slots_and_batches(self):
        direct = {0x61: (38, False)}
        batches = cu.plan_typing([0xE9, 0x61, 0xE9, 0xE7, 0xE3], direct, 2)
        self.assertEqual(batches, [
            ([0xE9, 0xE7], [("scratch", 0), ("key", 38, False), ("scratch", 0), ("scratch", 1)]),
            ([0xE3], [("scratch", 0)]),
        ])

    def test_plan_without_scratch(self):
        self.assertEqual(cu.plan_typing([0x61], {0x61: (38, False)}, 0), [([], [("key", 38, False)])])
        with self.assertRaises(cu.CuError) as cm:
            cu.plan_typing([0xE9], {}, 0)
        self.assertEqual(cm.exception.code, "no_scratch_keycode")

    def test_type_validation_rejects_controls(self):
        with self.assertRaises(cu.CuError):
            cu.validate_action({"action": "type", "text": "a\x1bb"})


class ScratchTypingTests(KeymapTestBase):
    def test_types_and_restores(self):
        rows, direct = self.layout()
        xd = FakeKeymapDisplay(rows, direct)
        out = xd.type_text("aé ça é")
        self.assertEqual(out, {"remapped_characters": 3})
        taps = [e for e in xd.log if e[0] == "tap"]
        self.assertEqual(taps[1], ("tap", (201,), ((0xE9, 0, 0xE9),)))
        self.assertFalse(cu.row_signature(xd.rows[200]))
        self.assertFalse(cu.row_signature(xd.rows[201]))
        # only one install per distinct keysym, one restore each
        self.assertEqual(sum(1 for e in xd.log if e[0] == "map"), 4)

    def test_direct_only_does_not_touch_keymap(self):
        rows, direct = self.layout()
        xd = FakeKeymapDisplay(rows, direct)
        self.assertEqual(xd.type_text("aB"), {"remapped_characters": 0})
        self.assertFalse([e for e in xd.log if e[0] == "map"])
        self.assertEqual(xd.log[1], ("tap", (50, 11), (None, (0x62, 0x42))))

    def test_batches_when_scratch_runs_out(self):
        rows, direct = self.layout(empty=(200,))
        xd = FakeKeymapDisplay(rows, direct)
        xd.type_text("éçé")
        taps = [e[2][0][0] for e in xd.log if e[0] == "tap"]
        self.assertEqual(taps, [0xE9, 0xE7, 0xE9])
        self.assertFalse(cu.row_signature(xd.rows[200]))

    def test_excludes_modifier_and_pressed_keycodes(self):
        rows, direct = self.layout(empty=(200, 201, 202))
        xd = FakeKeymapDisplay(rows, direct, modifiers={202}, pressed={201})
        xd.type_text("é")
        self.assertIn(("tap", (200,), ((0xE9, 0, 0xE9),)), xd.log)

    def test_rejects_caps_lock_before_input(self):
        rows, direct = self.layout()
        xd = FakeKeymapDisplay(rows, direct, mask=0x02)
        with self.assertRaises(cu.CuError) as cm:
            xd.type_text("é")
        self.assertEqual(cm.exception.code, "keyboard_state")
        self.assertEqual(xd.log, [])

    def test_no_scratch_fails_before_input(self):
        rows, direct = self.layout(empty=())
        xd = FakeKeymapDisplay(rows, direct)
        with self.assertRaises(cu.CuError) as cm:
            xd.type_text("aé")
        self.assertEqual(cm.exception.code, "no_scratch_keycode")
        self.assertEqual(xd.log, [])

    def test_interrupt_restores(self):
        rows, direct = self.layout()
        xd = FakeKeymapDisplay(rows, direct)

        def boom(codes):
            raise KeyboardInterrupt
        xd.tap = boom
        with self.assertRaises(KeyboardInterrupt):
            xd.type_text("é")
        self.assertFalse(cu.row_signature(xd.rows[201]))

    def test_conflicting_change_is_left_alone(self):
        rows, direct = self.layout()
        xd = FakeKeymapDisplay(rows, direct)
        orig_tap = xd.tap

        def tap(codes):
            orig_tap(codes)
            xd.rows[201] = (0x20AC, 0x20AC)  # another client rebinds our key
        xd.tap = tap
        out = xd.type_text("é")
        self.assertEqual(out["keymap_conflicts"], [201])
        self.assertEqual(xd.rows[201], (0x20AC, 0x20AC))

    def write_journal(self, keys, mode=0o600):
        path = cu.keymap_state_paths()[1]
        with open(path, "w") as fh:
            json.dump({"pid": 1, "keys": keys}, fh)
        os.chmod(path, mode)
        return path

    def test_recovers_after_killed_run(self):
        rows, direct = self.layout()
        rows[201] = (0xE9, 0xE9)  # left behind by a killed run
        rows[200] = (0x20AC, 0x20AC)  # recorded too, but since changed by someone else
        path = self.write_journal({"201": [[0xE9, 0xE9]], "200": [[0xE7, 0xE7]]})
        xd = FakeKeymapDisplay(rows, direct)
        out = xd.type_text("ç")
        self.assertFalse(cu.row_signature(xd.rows[201]))
        self.assertEqual(xd.rows[200], (0x20AC, 0x20AC))
        self.assertEqual(out["keymap_conflicts"], [200])
        with open(path) as fh:
            self.assertEqual(json.load(fh)["keys"], {})

    def test_recovers_row_rewritten_by_server(self):
        rows, direct = self.layout()
        rows[201] = (0xE9, 0, 0xE9, 0)  # how the server reports the [e, e] we installed
        self.write_journal({"201": [[0xE9, 0xE9]]})
        xd = FakeKeymapDisplay(rows, direct)
        out = xd.type_text("a")
        self.assertFalse(cu.row_signature(xd.rows[201]))
        self.assertNotIn("keymap_conflicts", out)

    def test_shift_only_rebinding_is_a_conflict(self):
        rows, direct = self.layout()
        xd = FakeKeymapDisplay(rows, direct)
        orig_tap = xd.tap

        def tap(codes):
            orig_tap(codes)
            xd.rows[201] = (0, 0xE9)  # same keysym, but shift-only: not ours
        xd.tap = tap
        out = xd.type_text("é")
        self.assertEqual(out["keymap_conflicts"], [201])
        self.assertEqual(xd.rows[201], (0, 0xE9))

    def test_readback_row_is_journaled(self):
        rows, direct = self.layout()
        xd = FakeKeymapDisplay(rows, direct)
        orig_tap = xd.tap
        seen = []

        def tap(codes):
            orig_tap(codes)
            with open(cu.keymap_state_paths()[1]) as fh:
                seen.append(json.load(fh)["keys"])
        xd.tap = tap
        xd.type_text("é")
        self.assertEqual(seen[0], {"201": [[0xE9, 0xE9], [0xE9, 0, 0xE9]]})

    def test_scratch_row_matches(self):
        m = cu.scratch_row_matches
        self.assertTrue(m((0xE9, 0, 0xE9, 0), (0xE9, 0xE9)))
        self.assertTrue(m((0xE9, 0xE9, 0xE9, 0xE9), (0xE9, 0xE9)))
        self.assertFalse(m((0, 0xE9), (0xE9, 0xE9)))
        self.assertFalse(m((0xE9, 0xC9), (0xE9, 0xE9)))
        self.assertTrue(m((0xE9, 0xC9, 0), (0xE9, 0xC9)))  # exact readback

    def test_stale_scratch_key_is_not_typed_directly(self):
        rows, direct = self.layout()
        rows[201] = (0xE9, 0xE9)  # stale entry from a killed run ...
        direct[0xE9] = (201, False)  # ... still in Xlib's cached XKB map
        self.write_journal({"201": [[0xE9, 0xE9]]})
        xd = FakeKeymapDisplay(rows, direct)
        xd.type_text("éç")
        typed = [e[2][0][0] for e in xd.log if e[0] == "tap"]
        self.assertEqual(typed, [0xE9, 0xE7])
        self.assertFalse(cu.row_signature(xd.rows[200]) | cu.row_signature(xd.rows[201]))

    def test_rejects_journal_that_is_not_private(self):
        rows, direct = self.layout()
        self.write_journal({"38": [[0x61, 0x41]]}, mode=0o644)
        xd = FakeKeymapDisplay(rows, direct)
        with self.assertRaises(cu.CuError) as cm:
            xd.type_text("é")
        self.assertEqual(cm.exception.code, "keymap_lock")
        self.assertEqual(xd.log, [])

    def test_conflict_between_batches_stops_without_overwriting(self):
        rows, direct = self.layout(empty=(200,))
        xd = FakeKeymapDisplay(rows, direct)
        orig_tap = xd.tap

        def tap(codes):
            orig_tap(codes)
            xd.rows[200] = (0x20AC, 0x20AC)
        xd.tap = tap
        with self.assertRaises(cu.CuError) as cm:
            xd.type_text("éç")
        self.assertEqual(cm.exception.code, "keymap_conflict")
        self.assertEqual(xd.rows[200], (0x20AC, 0x20AC))

    def test_stale_level_in_xkb_cache_is_not_trusted(self):
        rows, direct = self.layout()
        rows[11] = (0x42, 0x62)  # core map now has B unshifted, b shifted ...
        direct[0x62] = (11, False)  # ... but the XKB cache still says b is unshifted
        xd = FakeKeymapDisplay(rows, direct)
        xd.type_text("b")
        taps = [e for e in xd.log if e[0] == "tap"]
        self.assertEqual(len(taps), 1)
        self.assertNotEqual(taps[0][1], (11,))  # never the stale unshifted key, which now gives "B"
        self.assertIn(0x62, taps[0][2][-1])

    def test_rejected_remap_keeps_journal(self):
        rows, direct = self.layout()
        xd = FakeKeymapDisplay(rows, direct)
        orig_set = xd.set_keycode_row

        def set_row(kc, row):
            orig_set(kc, row)
            xd._errors.append(True)
        xd.set_keycode_row = set_row
        with self.assertRaises(cu.CuError) as cm:
            xd.type_text("é")
        self.assertEqual(cm.exception.code, "keymap_change_failed")
        with open(cu.keymap_state_paths()[1]) as fh:
            self.assertEqual(json.load(fh)["keys"], {"201": [[0xE9, 0xE9]]})

    def test_fifo_journal_does_not_block(self):
        os.mkfifo(cu.keymap_state_paths()[1], 0o600)
        rows, direct = self.layout()
        with self.assertRaises(cu.CuError) as cm:
            FakeKeymapDisplay(rows, direct).type_text("é")
        self.assertEqual(cm.exception.code, "keymap_lock")

    def test_interrupt_during_remap_is_recoverable(self):
        rows, direct = self.layout(empty=(200,))
        xd = FakeKeymapDisplay(rows, direct)
        orig_set = xd.set_keycode_row
        calls = []

        def set_row(kc, row):
            calls.append(row)
            if len(calls) == 2:  # second batch: journal written, X not yet changed
                raise KeyboardInterrupt
            orig_set(kc, row)
        xd.set_keycode_row = set_row
        with self.assertRaises(KeyboardInterrupt):
            xd.type_text("éç")
        self.assertFalse(cu.row_signature(xd.rows[200]))
        with open(cu.keymap_state_paths()[1]) as fh:
            self.assertEqual(json.load(fh)["keys"], {})


class ClickHoverTests(unittest.TestCase):
    """The click runner with a fake display: when it pauses, and what it reports."""

    class FakePointerDisplay:
        width, height = 100, 100

        def __init__(self, at, log):
            self.at, self.log = dict(at), log

        def pointer(self):
            return dict(self.at)

        def move(self, x, y):
            self.at = {"x": x, "y": y}
            self.log.append(("move", x, y))

        def button(self, number, count=1):
            self.log.append(("button", number, count))

    def click(self, start, **fields):
        log = []
        session = cu.Session()
        session._xd = self.FakePointerDisplay(start, log)
        params = cu.validate_action(dict({"action": "click", "x": 10, "y": 20}, **fields))
        with unittest.mock.patch.object(cu.time, "sleep", side_effect=lambda s: log.append(("sleep", s))):
            result = session.run(params)
        return result, log

    def test_default_hover_pauses_between_move_and_press(self):
        result, log = self.click({"x": 0, "y": 0})
        self.assertEqual(log, [("move", 10, 20), ("sleep", cu.DEFAULT_HOVER), ("button", 1, 1)])
        self.assertEqual(result["hover"], cu.DEFAULT_HOVER)

    def test_custom_hover(self):
        result, log = self.click({"x": 0, "y": 0}, hover=0.4)
        self.assertEqual(log[1], ("sleep", 0.4))
        self.assertEqual(result["hover"], 0.4)

    def test_hover_zero_keeps_old_short_settle(self):
        result, log = self.click({"x": 0, "y": 0}, hover=0)
        self.assertEqual(log, [("move", 10, 20), ("sleep", cu.CLICK_SETTLE), ("button", 1, 1)])
        self.assertEqual(result["hover"], 0.0)

    def test_pointer_already_on_target_skips_hover(self):
        result, log = self.click({"x": 10, "y": 20}, hover=2)
        self.assertEqual(log, [("move", 10, 20), ("sleep", cu.CLICK_SETTLE), ("button", 1, 1)])
        self.assertEqual(result["hover"], 0.0)

    def test_repeated_click_on_same_spot_pauses_once(self):
        log = []
        session = cu.Session()
        session._xd = self.FakePointerDisplay({"x": 0, "y": 0}, log)
        params = cu.validate_action({"action": "click", "x": 10, "y": 20})
        with unittest.mock.patch.object(cu.time, "sleep", side_effect=lambda s: log.append(("sleep", s))):
            hovers = [session.run(params)["hover"] for _ in range(2)]
        self.assertEqual(hovers, [cu.DEFAULT_HOVER, 0.0])


class FakeSelection:
    """Scripted selection owner for read_selection.

    replies maps a target to None (refused), "silent", (type, bytes) or
    ("INCR", [chunk, ...]). Chunks are delivered one per property delete.
    """

    selection = "CLIPBOARD"

    def __init__(self, replies, owner=True, chunk_type="UTF8_STRING"):
        self.replies, self.owner, self.chunk_type = replies, owner, chunk_type
        self.events, self.prop, self.chunks, self.requested = [], None, None, []

    def has_owner(self):
        return self.owner

    def convert(self, target):
        self.requested.append(target)
        reply = self.replies.get(target)
        self.events.append(("property", True))  # unrelated noise before the answer
        if reply == "silent":
            return
        if reply is None:
            self.events.append(("selection", False))
            return
        type_name, data = reply
        if type_name == "INCR":
            self.prop, self.chunks = ("INCR", 32, b"\0" * 8), list(data) + [b""]
        else:
            self.prop = (type_name, 8, data)
        self.events.append(("selection", True))

    def next_event(self, timeout):
        return self.events.pop(0) if self.events else None

    def get_property(self, delete):
        prop, self.prop = self.prop, None
        self.events.append(("property", False))  # our own delete
        if self.chunks:
            self.prop = (self.chunk_type, 8, self.chunks.pop(0))
            self.events.append(("property", True))
        return prop or (None, 0, b"")


class ClipboardTests(unittest.TestCase):
    def read(self, conn, **kw):
        return cu.read_selection(conn, timeout=0.01, **kw)

    def error(self, conn, **kw):
        with self.assertRaises(cu.CuError) as cm:
            self.read(conn, **kw)
        return cm.exception.code

    def test_utf8(self):
        conn = FakeSelection({"UTF8_STRING": ("UTF8_STRING", "héllo ✓ 😀\n".encode())})
        self.assertEqual(self.read(conn), ("UTF8_STRING", "héllo ✓ 😀\n"))
        self.assertEqual(conn.requested, ["UTF8_STRING"])

    def test_falls_back_to_string_as_latin1(self):
        conn = FakeSelection({"STRING": ("STRING", "café".encode("latin-1"))})
        self.assertEqual(self.read(conn), ("STRING", "café"))
        self.assertEqual(conn.requested, list(cu.TEXT_TARGETS))

    def test_decode_uses_reported_type(self):
        conn = FakeSelection({"UTF8_STRING": ("STRING", b"caf\xe9")})
        self.assertEqual(self.read(conn), ("STRING", "café"))
        self.assertEqual(cu.decode_selection_text(b"ok\0\0", "UTF8_STRING"), "ok")
        self.assertEqual(cu.decode_selection_text(b"bad \xff", "UTF8_STRING"), "bad �")

    def test_empty_text(self):
        self.assertEqual(self.read(FakeSelection({"UTF8_STRING": ("UTF8_STRING", b"")})), ("UTF8_STRING", ""))

    def test_incr_assembly(self):
        text = "日本語 " * 5000
        data = text.encode()
        chunks = [data[i:i + 4097] for i in range(0, len(data), 4097)]  # splits inside characters
        conn = FakeSelection({"UTF8_STRING": ("INCR", chunks)})
        self.assertEqual(self.read(conn), ("UTF8_STRING", text))
        self.assertIsNone(conn.prop)
        self.assertEqual(conn.chunks, [])

    def test_incr_with_no_chunks(self):
        self.assertEqual(self.read(FakeSelection({"UTF8_STRING": ("INCR", [])})), ("UTF8_STRING", ""))

    def test_incr_owner_stalls(self):
        conn = FakeSelection({"UTF8_STRING": ("INCR", [b"abc", b"def"])})
        get = conn.get_property

        def stall_after_first(delete):
            out = get(delete)
            if out[2] == b"abc":
                conn.events = []
            return out
        conn.get_property = stall_after_first
        self.assertEqual(self.error(conn), "clipboard_timeout")

    def test_errors(self):
        self.assertEqual(self.error(FakeSelection({}, owner=False)), "clipboard_empty")
        self.assertEqual(self.error(FakeSelection({})), "unsupported_target")
        self.assertEqual(self.error(FakeSelection({"UTF8_STRING": "silent"})), "clipboard_timeout")

    def test_size_limit(self):
        self.assertEqual(self.error(FakeSelection({"UTF8_STRING": ("UTF8_STRING", b"x" * 11)}), limit=10),
                         "clipboard_too_large")
        incr = FakeSelection({"UTF8_STRING": ("INCR", [b"x" * 6, b"x" * 6])})
        self.assertEqual(self.error(incr, limit=10), "clipboard_too_large")

    def test_validation(self):
        self.assertEqual(cu.validate_action({"action": "clipboard"}),
                         {"action": "clipboard", "selection": "clipboard", "out": None})
        self.assertEqual(cu.validate_action({"action": "clipboard", "selection": "primary", "out": "/x"})["out"], "/x")
        for bad in ({"selection": "secondary"}, {"selection": 1}, {"out": ""}, {"out": 3}, {"text": "x"}):
            with self.assertRaises(cu.CuError):
                cu.validate_action(dict(bad, action="clipboard"))

    def run_clipboard(self, argv, text="héllo"):
        class FakeConn:
            def __init__(self, xd, selection):
                self.selection = selection

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False
        buf = io.StringIO()
        with unittest.mock.patch.object(cu, "SelectionConn", FakeConn), \
                unittest.mock.patch.object(cu, "read_selection", lambda conn: ("UTF8_STRING", text)), \
                unittest.mock.patch.object(cu.Session, "xd", object()), redirect_stdout(buf):
            code = cu.main(argv)
        return code, json.loads(buf.getvalue())

    def test_cli_prints_text(self):
        code, out = self.run_clipboard(["clipboard"])
        self.assertEqual(code, 0)
        self.assertEqual((out["text"], out["length"], out["selection"]), ("héllo", 5, "clipboard"))

    def test_cli_out_writes_private_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "c.txt")
            code, out = self.run_clipboard(["clipboard", "--selection", "primary", "--out", path])
            self.assertEqual(code, 0)
            self.assertNotIn("text", out)
            self.assertEqual((out["path"], out["length"], out["bytes"], out["selection"]), (path, 5, 6, "primary"))
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "héllo")
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(os.listdir(d), ["c.txt"])
            code, out = self.run_clipboard(["clipboard", "--out", os.path.join(d, "missing", "c.txt")])
            self.assertEqual((code, out["error"]["code"]), (cu.EXIT_RUNTIME, "bad_path"))


def frame(rows, bypp=4):
    """A wait frame from a list of rows of pixel ints."""
    h, w = len(rows), len(rows[0])
    data = b"".join(b"".join(v.to_bytes(bypp, "little") for v in row) for row in rows)
    return {"layout": (w, h, bypp, w * bypp), "pixels": data}


class FakeProbe:
    """Scripted window/selection/frame values; the last value repeats."""

    def __init__(self, windows=(), frames=(), selections=()):
        self.windows, self.frames, self.selections = list(windows), list(frames), list(selections)
        self.calls = []

    @staticmethod
    def _next(values):
        return values.pop(0) if len(values) > 1 else values[0]

    def window(self):
        self.calls.append("window")
        wid, title = self._next(self.windows)
        return {"window": wid, "title": title}

    def frame(self):
        self.calls.append("frame")
        return self._next(self.frames)

    def selection(self, name, base=None):
        self.calls.append(("selection", name))
        return dict(self._next(self.selections))


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t = round(self.t + s, 6)  # avoid float drift in poll counts


def sel(owner=1, timestamp=None, digest=None, length=None):
    return {"owner": owner, "timestamp": timestamp, "digest": digest, "length": length}


class WaitConditionTests(unittest.TestCase):
    def cond(self, spec, **fields):
        return cu.validate_action(dict({"action": "wait", "until": spec}, **fields))["until"]

    def wait(self, cond, probe, baseline=None):
        clock = FakeClock()
        return cu.wait_until(cond, probe, baseline, clock=clock, sleep=clock.sleep)

    def timeout(self, cond, probe, baseline=None):
        with self.assertRaises(cu.CuError) as cm:
            self.wait(cond, probe, baseline)
        self.assertEqual((cm.exception.code, cm.exception.exit_code), ("wait_timeout", cu.EXIT_RUNTIME))
        return cm.exception.extra

    def test_parse_conditions(self):
        self.assertEqual(cu.parse_condition("title-change"), {"name": "title-change"})
        self.assertEqual(cu.parse_condition("title-contains=a=b"), {"name": "title-contains", "text": "a=b"})
        self.assertEqual(cu.parse_condition("stable"), {"name": "stable", "seconds": cu.DEFAULT_STABLE_SECONDS})
        self.assertEqual(cu.parse_condition("stable=2")["seconds"], 2.0)
        self.assertEqual(cu.parse_condition("clipboard-change")["selection"], "clipboard")
        self.assertEqual(cu.parse_condition("clipboard-change=primary")["selection"], "primary")
        for bad in ("", "nope", "title-change=x", "title-contains", "title-contains=", "title-matches=(",
                    "stable=0", "stable=61", "stable=nan", "stable=x", "clipboard-change=secondary", 3, None):
            with self.assertRaises(cu.CuError, msg=bad):
                cu.parse_condition(bad)

    def test_wait_validation(self):
        self.assertEqual(self.cond("title-change"), {"name": "title-change", "timeout": 10.0})
        self.assertEqual(self.cond("stable", timeout=2, tolerance=5),
                         {"name": "stable", "seconds": 0.5, "timeout": 2.0, "tolerance": 5})
        self.assertEqual(self.cond("title-change", **{"from": "Old"})["from"], {"window": None, "title": "Old"})
        self.assertEqual(self.cond("window-change", **{"from": "0x1a"})["from"]["window"], 0x1A)
        self.assertEqual(self.cond("window-change", **{"from": 26})["from"]["window"], 26)
        bad = [{"seconds": 1, "until": "title-change"}, {}, {"seconds": 1, "timeout": 2},
               {"until": "title-change", "timeout": 61}, {"until": "title-change", "timeout": -1},
               {"until": "title-change", "timeout": True}, {"until": "title-change", "tolerance": 1},
               {"until": "stable", "tolerance": -1}, {"until": "stable", "tolerance": 1.5},
               {"until": "stable=5", "timeout": 2}, {"until": "stable", "from": "x"},
               {"until": "window-change", "from": "nope"}, {"until": "title-change", "from": 3},
               {"until": "title-change", "bogus": 1}]
        for item in bad:
            with self.assertRaises(cu.CuError, msg=item):
                cu.validate_action(dict(item, action="wait"))

    def test_wait_for_on_actions(self):
        p = cu.validate_action({"action": "key", "keys": "Return", "wait_for": "title-change", "timeout": 3})
        self.assertEqual(p["wait_for"], {"name": "title-change", "timeout": 3.0})
        self.assertNotIn("wait_for", cu.validate_action({"action": "key", "keys": "Return"}))
        for item in ({"action": "key", "keys": "a", "timeout": 3}, {"action": "key", "keys": "a", "wait_for": "x"},
                     {"action": "info", "wait_for": "title-change"},
                     {"action": "clipboard", "wait_for": "clipboard-change"},
                     {"action": "key", "keys": "a", "wait_for": "title-change", "from": "x"}):
            with self.assertRaises(cu.CuError, msg=item):
                cu.validate_action(item)

    def test_title_change(self):
        probe = FakeProbe(windows=[(1, "A"), (1, "A"), (1, "A"), (1, "B")])
        out = self.wait(self.cond("title-change"), probe)
        self.assertEqual((out["met"], out["title"], out["from"], out["window"], out["polls"]),
                         (True, "B", "A", "0x1", 3))
        self.assertAlmostEqual(out["elapsed"], 0.2)

    def test_baseline_from_before_the_action(self):
        probe = FakeProbe(windows=[(1, "B")])  # already changed when the wait starts
        self.assertEqual(self.wait(self.cond("title-change"), probe, {"window": 1, "title": "A"})["polls"], 1)
        self.assertEqual(probe.calls, ["window"])
        cond = self.cond("title-change", **{"from": "A"})
        self.assertTrue(self.wait(cond, FakeProbe(windows=[(1, "B")]), {"window": 1, "title": "B"})["met"])

    def test_timeout_reports_last_value(self):
        extra = self.timeout(self.cond("title-change", timeout=1), FakeProbe(windows=[(1, "A")]))
        self.assertEqual((extra["met"], extra["title"], extra["from"], extra["polls"]), (False, "A", "A", 11))
        self.assertAlmostEqual(extra["elapsed"], 1.0)
        extra = self.timeout(self.cond("title-change", timeout=0), FakeProbe(windows=[(1, "A")]))
        self.assertEqual(extra["polls"], 1)

    def test_title_contains_and_matches(self):
        probe = FakeProbe(windows=[(1, None), (1, "Loading"), (1, "Example Domain — Firefox")])
        self.assertEqual(self.wait(self.cond("title-contains=Example"), probe)["polls"], 3)
        probe = FakeProbe(windows=[(1, "Page 1"), (1, "Page 12")])
        out = self.wait(self.cond("title-matches=Page \\d{2}"), probe)
        self.assertEqual((out["polls"], out["pattern"]), (2, "Page \\d{2}"))
        self.timeout(self.cond("title-contains=example", timeout=0.3), FakeProbe(windows=[(1, "Example")]))

    def test_lost_window_title_is_not_a_change(self):
        probe = FakeProbe(windows=[(1, "A")])
        lost = {"window": 1, "title": None, "lost": True}
        probe.window = lambda: dict(lost)
        self.timeout(self.cond("title-change", timeout=0.2), probe, {"window": 1, "title": "A"})
        self.assertFalse(cu.condition_met(self.cond("title-contains=A"), None, lost))
        self.assertTrue(cu.condition_met(self.cond("window-change"), {"window": 2, "title": "A"}, lost))

    def test_regex_overflow_is_a_usage_error(self):
        with self.assertRaises(cu.CuError) as cm:
            cu.parse_condition("title-matches=a{4294967296}")
        self.assertEqual(cm.exception.exit_code, cu.EXIT_USAGE)

    def test_probe_deadline_is_reset(self):
        probe = FakeProbe(windows=[(1, "A")])
        probe.deadline = None
        self.timeout(self.cond("title-change", timeout=0.1), probe)
        self.assertIsNone(probe.deadline)

    def test_window_change(self):
        probe = FakeProbe(windows=[(1, "A"), (1, "A"), (2, "Save As")])
        out = self.wait(self.cond("window-change"), probe)
        self.assertEqual((out["window"], out["from"], out["title"]), ("0x2", "0x1", "Save As"))
        out = self.wait(self.cond("window-change"), FakeProbe(windows=[(1, "A"), (None, None)]))
        self.assertIsNone(out["window"])

    def test_changed_pixels(self):
        a = frame([[1, 2, 3], [4, 5, 6]])
        self.assertEqual(cu.changed_pixels(a, frame([[1, 2, 3], [4, 5, 6]]), 0), 0)
        self.assertEqual(cu.changed_pixels(a, frame([[1, 9, 3], [9, 5, 9]]), 10), 3)
        self.assertEqual(cu.changed_pixels(a, frame([[9, 9, 9], [9, 9, 9]]), 1), 3)  # stops after a row
        self.assertEqual(cu.changed_pixels(frame([[1, 2]], 3), frame([[1, 3]], 3), 5), 1)
        self.assertEqual(cu.changed_pixels(a, frame([[1, 2, 3]]), 100), float("inf"))
        self.assertEqual(cu.changed_pixels(dict(a, colormap=b"x"), dict(a, colormap=b"y"), 100), float("inf"))

    def test_xwd_frame_skips_row_padding(self):
        data = make_xwd(3, 2, PIX, pad=4)
        f = cu.xwd_frame(data)
        self.assertEqual(f["layout"], (3, 2, 4, 16))
        pad_at = 105 + 12  # header + name, then the first row's 4 padding bytes
        g = cu.xwd_frame(data[:pad_at] + b"\x01" * 4 + data[pad_at + 4:])
        self.assertNotEqual(f["pixels"], g["pixels"])
        self.assertEqual(cu.changed_pixels(f, g, 0), 0)

    def test_stable(self):
        still, moved = frame([[0, 0], [0, 0]]), frame([[1, 0], [0, 0]])
        probe = FakeProbe(frames=[still, moved, still, still])
        out = self.wait(self.cond("stable"), probe)  # anchors reset at moved and at still again
        self.assertTrue(out["met"])
        self.assertGreaterEqual(out["stable_for"], 0.5)
        self.assertEqual(out["polls"], 8)  # 0.5 s after the last reset at poll 3

    def test_stable_tolerance_and_timeout(self):
        a, b = frame([[0, 0], [0, 0]]), frame([[1, 0], [0, 0]])
        blinking = [a, b] * 50
        extra = self.timeout(self.cond("stable", timeout=2), FakeProbe(frames=list(blinking)))
        self.assertLess(extra["stable_for"], 0.5)
        self.assertIn("tolerance", cu.TIMEOUT_HINTS["stable"])
        out = self.wait(self.cond("stable", tolerance=1), FakeProbe(frames=list(blinking)))
        self.assertEqual(out["polls"], 6)

    def test_selection_changed(self):
        self.assertTrue(cu.selection_changed(sel(None), sel(1)))
        self.assertTrue(cu.selection_changed(sel(1), sel(2)))
        self.assertTrue(cu.selection_changed(sel(1), sel(None)))
        self.assertFalse(cu.selection_changed(sel(None), sel(None)))
        self.assertTrue(cu.selection_changed(sel(1, 5, "x"), sel(1, 6, "x")))  # same text copied again
        self.assertTrue(cu.selection_changed(sel(1, None, "x"), sel(1, None, "y")))
        self.assertFalse(cu.selection_changed(sel(1, 5, "x"), sel(1, 5, "x")))
        self.assertFalse(cu.selection_changed(sel(1, 5, "x"), sel(1, None, None)))  # busy owner: unknown

    def test_clipboard_change(self):
        probe = FakeProbe(selections=[sel(1, 5, "x"), sel(1, 5, "x"), sel(1, 5, "y", 3)])
        out = self.wait(self.cond("clipboard-change=primary"), probe)
        self.assertEqual((out["selection"], out["length"], out["polls"]), ("primary", 3, 2))
        extra = self.timeout(self.cond("clipboard-change", timeout=0.2), FakeProbe(selections=[sel(7, 5)]))
        self.assertEqual((extra["owner"], extra["timestamp"]), ("0x7", 5))
        self.assertEqual(probe.calls, [("selection", "primary")] * 3)


class FakeSelectionConn:
    """SelectionConn stand-in for DesktopProbe.selection."""
    owner_value, opened = 7, []

    def __init__(self, xd, selection):
        FakeSelectionConn.opened.append(selection)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def owner(self):
        return self.owner_value


class ProbeSelectionTests(unittest.TestCase):
    def snapshot(self, base=None, stamp=5, text="héllo"):
        FakeSelectionConn.opened = []
        reads = []

        def read(conn, timeout):
            reads.append(timeout)
            if isinstance(text, Exception):
                raise text
            return "UTF8_STRING", text
        session = cu.Session()
        session._xd = object()
        with unittest.mock.patch.object(cu, "SelectionConn", FakeSelectionConn), \
                unittest.mock.patch.object(cu, "selection_timestamp", lambda conn, t: stamp), \
                unittest.mock.patch.object(cu, "read_selection", read):
            return session.probe.selection("clipboard", base), reads

    def test_full_snapshot(self):
        state, reads = self.snapshot()
        self.assertEqual((state["owner"], state["timestamp"], state["length"]), (7, 5, 5))
        self.assertEqual(len(state["digest"]), 64)
        self.assertEqual(reads, [cu.SELECTION_PROBE_TIMEOUT])

    def test_skips_text_once_owner_or_timestamp_differ(self):
        state, reads = self.snapshot(base=sel(8, 5, "d"))
        self.assertEqual((state["timestamp"], reads, FakeSelectionConn.opened), (None, [], ["CLIPBOARD"]))
        state, reads = self.snapshot(base=sel(7, 4, "d"))
        self.assertEqual((state["timestamp"], reads), (5, []))
        state, reads = self.snapshot(base=sel(7, 5, "d"))
        self.assertEqual(len(reads), 1)

    def test_unreadable_text(self):
        state, _ = self.snapshot(text=cu.CuError("clipboard_timeout", "slow"))
        self.assertEqual((state["owner"], state["digest"]), (7, None))


class SessionWaitTests(unittest.TestCase):
    """Baselines are taken at the action's commit point, before the input is sent."""

    class FakeXd:
        width, height = 100, 100

        def __init__(self, log):
            self.log = log

        def press_chord(self, keys):
            self.log.append(("key", keys))

        def type_text(self, text):
            self.log.append(("type", text))
            return {"remapped_characters": 0}

    def session(self, titles):
        log = []
        session = cu.Session()
        session._xd = self.FakeXd(log)
        titles = list(titles)

        def window():
            log.append("probe")
            return {"window": 1, "title": titles.pop(0) if len(titles) > 1 else titles[0]}
        session.probe.window = window
        return session, log

    def test_key_wait_for(self):
        session, log = self.session(["A", "B"])
        p = cu.validate_action({"action": "key", "keys": "Return", "wait_for": "title-change"})
        result = session.run(p)
        self.assertEqual(log, ["probe", ("key", "Return"), "probe"])
        self.assertEqual((result["wait"]["from"], result["wait"]["title"]), ("A", "B"))

    def test_open_tab_baseline_after_new_tab(self):
        session, log = self.session(["New Tab", "Example"])
        p = cu.validate_action({"action": "open-tab", "url": "http://x", "wait_for": "title-change"})
        with unittest.mock.patch.object(cu.time, "sleep"):
            result = session.run(p)
        self.assertEqual(log, [("key", "ctrl+t"), ("type", "http://x"), "probe", ("key", "Return"), "probe"])
        self.assertEqual(result["wait"]["from"], "New Tab")
        self.assertNotIn("next", result)

    def test_timeout_keeps_action_result(self):
        session, log = self.session(["A"])
        p = cu.validate_action({"action": "key", "keys": "Return", "wait_for": "title-change", "timeout": 0})
        with self.assertRaises(cu.CuError) as cm:
            session.run(p)
        self.assertEqual(cm.exception.extra["action_result"], {"action": "key", "keys": "Return"})

    def test_batch_wait_baseline_before_previous_action(self):
        log = []
        titles = ["A", "B"]

        def window(self):
            log.append("probe")
            return {"window": 1, "title": titles.pop(0) if len(titles) > 1 else titles[0]}

        class Xd(self.FakeXd):
            def resolve_chord(self, keys):
                return [1]
        batch = [{"action": "key", "keys": "Return"}, {"action": "wait", "until": "title-change"},
                 {"action": "wait", "until": "title-contains=B"}]
        with unittest.mock.patch.object(cu.DesktopProbe, "window", window), \
                unittest.mock.patch.object(cu.Session, "xd", Xd(log)):
            out, code = cu.run_batch(batch)
        self.assertEqual(code, 0)
        self.assertEqual(log, ["probe", ("key", "Return"), "probe", "probe"])
        self.assertEqual((out["results"][1]["from"], out["results"][1]["title"]), ("A", "B"))

    def test_cli_wait_conditions(self):
        def item(*argv):
            return cu.wait_item(cu.build_parser().parse_args(["wait"] + list(argv)))
        self.assertEqual(item("1"), {"action": "wait", "seconds": 1.0})
        self.assertEqual(item("--stable"), {"action": "wait", "until": "stable=0.5"})
        self.assertEqual(item("--stable", "2", "--timeout", "5"), {"action": "wait", "until": "stable=2.0", "timeout": 5.0})
        self.assertEqual(item("--clipboard-change", "--selection", "primary")["until"], "clipboard-change=primary")
        self.assertEqual(item("--title-change", "--from", "Old")["from"], "Old")
        self.assertEqual(item("--title-contains", "a b")["until"], "title-contains=a b")
        for argv in (["--title-change", "--window-change"], ["--selection", "primary"]):
            with self.assertRaises(cu.CuError):
                item(*argv)
        for argv in (["wait"], ["wait", "1", "--title-change"], ["wait", "--stable", "--tolerance", "1.5"],
                     ["key", "a", "--wait-for", "stable", "--tolerance", "-1"], ["wait", "--title-contains", ""]):
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(cu.main(argv), cu.EXIT_USAGE, argv)
