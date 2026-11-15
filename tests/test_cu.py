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
