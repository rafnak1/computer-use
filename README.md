<img src="assets/icon.svg" width="64" height="64" alt="">

# computer-use

`cu` is a small command-line tool that lets an AI agent, or a script, drive
the current X11 desktop. It takes screenshots, reports the focused window,
moves and clicks the mouse, types text, presses keys, scrolls and reads
copied text from the clipboard.
Every command prints one JSON object, so agents can parse the result.
Only `--help`, `--version` and running `cu` with no command print plain
text.

It is one Python 3 file with no pip dependencies. Input goes through
libX11/libXtst via `ctypes`, screenshots come from `xwd` and are encoded to
PNG in-process, and window metadata comes from `xprop`.

## Scope

`cu` is meant to be a thin, predictable layer between an agent and one
desktop:

- **What it does:** full-desktop PNG screenshots; focused-window title,
  class, PID and geometry; display size and pointer position; pointer
  move, click (left/middle/right, single or multi) and wheel scroll; key
  chords; literal text entry; reading text from the clipboard or PRIMARY
  selection; a browser "open tab" shortcut; timed waits; and JSON batches
  that run several of these in order.
- **Who it is for:** agents that decide what to do by looking at
  screenshots. Click coordinates are the same native pixels as the
  screenshot, with no scaling.
- **Where it runs:** a single local X11 session on Linux.

It is deliberately not a browser-automation framework, a test runner, a
remote-desktop tool or a cross-platform input library.

## Limitations

- **X11 only.** It does not support Wayland sessions, except for apps
  running under XWayland, and even then screenshots and input may be
  incomplete. macOS and Windows are not supported. The session must be
  unlocked, and the caller needs access to the display (`$DISPLAY`,
  default `:0`, plus `$XAUTHORITY` if your setup needs it).
- **No understanding of the screen.** There is no DOM access, OCR,
  accessibility tree or element lookup. The caller has to read the
  screenshots itself.
- **No load or success detection.** Exit code 0 means the input was sent,
  not that the app reacted. After navigating, run `cu wait 1`, take a new
  screenshot and wait again if it still shows loading. Smooth scrolling may
  also need a short wait.
- **Keyboard layout and Unicode.** `type` types characters found on the
  plain or Shift level of the first keyboard layout directly. Any other
  character (accents, AltGr symbols, other scripts, emoji) is typed by
  briefly binding it to an unused keycode, pressing that key, and then
  resetting the keycode, the same technique xdotool uses. This changes the
  shared X keymap for a moment. A display-scoped lock keeps two `cu`
  processes from clashing, and if a run is killed the next run restores
  what it left. Keycodes that another program changed in the meantime are
  left alone and reported as `keymap_conflicts`. Apps re-read the keymap
  asynchronously, so `cu` waits briefly around each change; a very slow
  app could still see the wrong key. The core keymap is restored, but
  some XKB details of those spare keys may differ afterwards. Whether a
  character appears also depends on the app and its font, especially for
  emoji. `type` refuses to start while Shift, Ctrl, Alt, Super or Caps Lock
  is active or a second layout group is selected, and fails with
  `no_scratch_keycode` if the layout has no unused keycode. The result
  reports `remapped_characters`. Long runs of remapped text (a few hundred
  distinct characters) can upset some window managers: under Openbox the
  next window stopped receiving keyboard focus. xdotool's remapping does
  the same, so prefer shorter `type` calls for large non-Latin texts.
- **`open-tab` is a shortcut, not an API.** It presses Ctrl+T in whatever
  window has focus, types the URL and presses Enter. Check with `cu info`
  that a browser is focused first.
- **Clipboard: read-only, text only.** `cu clipboard` reads text that an
  app has already copied. It cannot set the clipboard, copy for you or read
  images and files (`unsupported_target`). It asks for `UTF8_STRING`, then
  `text/plain;charset=utf-8`, then `STRING`, and supports incremental (INCR)
  transfers for large text, up to 64 MiB. It fails with `clipboard_empty`
  when no app owns the selection and `clipboard_timeout` when the owner stays
  silent for 3 seconds. Apps take the clipboard asynchronously, so wait
  briefly after `cu key ctrl+c`; until they do, you read the previous
  contents. If the app that copied has exited, the clipboard is usually
  gone too, unless a clipboard manager keeps it.
- **One screen, full captures.** Screenshots always cover the whole root
  window. There is no per-window or region capture, and with several
  monitors you get a single combined image.
- **Synthetic input.** Events go through the XTEST extension. Some apps,
  games or security prompts may ignore or treat that input differently.
- **Limits:** wait 0–60 s, scroll 1–50 ticks per call, click count 1–5,
  click hover 0–5 s, text up to 10,000 characters per call, clipboard up to
  64 MiB.

## Safety

`cu` acts on your real desktop with your permissions. Anything an agent
can see on screen it can also screenshot, and anything you could click it
can click. So:

- Run it in a dedicated user session, VM or container when an agent
  drives it unattended.
- Keep private windows and unrelated tabs out of reach of the agent.
- The clipboard often holds passwords, tokens or other private text that
  you copied earlier. `cu clipboard` hands all of it to the agent, and
  PRIMARY holds whatever was last selected anywhere. Clear the clipboard
  before handing over the session, and do not copy secrets while an agent
  is running. `--out` files are created with mode 0600.
- Prefer short batches followed by a screenshot over long blind sequences.
- Screenshots are saved as ordinary files. Default paths go to the system
  temp directory; pass an explicit path if that directory is shared.

## Requirements

- Python 3 (developed on 3.14; CI runs 3.9 and 3.13)
- libX11 and libXtst, and an X server with the XTEST extension
- `xwd` and `xprop`

On Debian/Ubuntu these come from `libx11-6 libxtst6 x11-apps x11-utils`.

## Install

```sh
git clone https://github.com/rafnak1/computer-use.git
mkdir -p ~/.local/bin
ln -s "$PWD/computer-use/bin/cu" ~/.local/bin/cu
cu --help
```

`~/.local/bin` must be on your `PATH`. You can also run `computer-use/bin/cu`
directly.

`cu --help` has a quick start, the coordinate rules, timing advice and the
batch JSON schema, so an agent can learn the tool from the CLI alone.

## Usage

```sh
cu info                        # focused window, display size, pointer
cu observe /tmp/state.png      # info + screenshot in one call
cu screenshot /tmp/before.png
cu open-tab https://example.com
cu key ctrl+f
cu type example
cu click 500 300 [--button left|middle|right] [--count N] [--hover SECONDS]
cu move 500 300
cu scroll down 3
cu wait 1
cu clipboard [--selection clipboard|primary] [--out FILE]
cu batch actions.json [--screenshot /tmp/final.png]   # "-" reads stdin
```

- Failures exit nonzero (2 for invalid input, 1 for runtime errors, 130
  when interrupted) and print an error object; `hint` is optional:
  `{"ok": false, "error": {"code": "out_of_bounds", "message": "...", "hint": "..."}}`.
- Chords join names with `+`: `ctrl+l`, `ctrl+shift+Tab`, `alt+Left`,
  `Return`. Unknown key names are rejected before any key is pressed.
- `type` sends literal text and does not touch the clipboard. Use `key` for
  shortcuts.
- `click` moves the pointer to the target and rests there for `--hover`
  seconds (default 0.15, max 5) before pressing, because many menus,
  dropdowns, date pickers and browser tabs ignore a click that arrives
  right after the pointer does. `--hover 0` clicks at once. The pause is
  skipped when the pointer is already at the target, so repeated clicks on
  one spot stay fast. The result reports the `hover` actually used (`0`
  when skipped).
- `scroll` sends wheel ticks at the current pointer position, so move over
  the target panel first.
- `clipboard` prints `{"ok": true, "action": "clipboard", "selection":
  "clipboard", "type": "UTF8_STRING", "length": N, "text": "..."}`, where
  `length` counts characters. With `--out FILE` the text goes to that file
  as UTF-8 and the JSON has `path` and `bytes` instead of `text`, which keeps
  the output small for long pages. `--selection primary` reads the last
  selected text instead of the last copied text.

### Reading text from the screen

Copying and reading the clipboard is usually faster and more accurate than
scrolling through screenshots:

```sh
cu key ctrl+a; cu key ctrl+c; cu wait 0.3
cu clipboard --out /tmp/page.txt
```

For the whole text of a web page in Firefox, use the devtools console
(Ctrl+Shift+J in Chrome):

```sh
cu key ctrl+shift+k; cu wait 1
cu type 'copy(document.body.innerText)'
cu key Return; cu wait 0.3
cu key F12                        # close devtools again
cu clipboard --out /tmp/page.txt
```

Check with `cu info` first that the right window has focus, since the keys
go wherever focus is.

### Batch

A batch is a JSON array (or `{"actions": [...]}`) of these actions: `info`;
`observe`/`screenshot` with optional `path`; `open-tab` with `url`; `key`
with `keys`; `type` with `text`; `click` with `x`, `y`, optional `button`,
`count` and `hover` (seconds); `move` with `x`, `y`; `scroll` with `direction` and optional
`amount`; `wait` with `seconds`; `clipboard` with optional `selection`
(`clipboard` or `primary`) and `out`.

```json
[
  {"action": "key", "keys": "ctrl+f"},
  {"action": "type", "text": "example"},
  {"action": "wait", "seconds": 0.5},
  {"action": "screenshot", "path": "/tmp/result.png"}
]
```

Copy, then read, in one call:

```json
[
  {"action": "key", "keys": "ctrl+a"},
  {"action": "key", "keys": "ctrl+c"},
  {"action": "wait", "seconds": 0.3},
  {"action": "clipboard", "out": "/tmp/page.txt"}
]
```

Before anything runs, every item is checked for a known action, known
fields and valid value types and ranges, and every `key` chord is resolved
on the display. Other checks happen only when an action runs: whether
coordinates are on screen, whether a screenshot path is writable, and
whether text can be typed. The batch then runs in order and stops at the
first runtime failure. Actions
that already ran stay applied, and the output reports `results`,
`completed` and `failed_index`.

## Testing

```sh
python3 -m unittest discover -s tests
```

The unit tests need no display. They cover XWD parsing and PNG output,
`xprop` parsing, argument and batch validation, JSON errors, and the
release of held mouse buttons, the Unicode typing plan and keymap
restore logic (against an in-memory fake keymap), and clipboard reading
(target fallback, INCR assembly, timeouts and size limits against a
scripted fake selection owner). One test that resolves key names is
skipped when `$DISPLAY` is unset; it does not send input.

`examples/form.html` is a local page for manual end-to-end checks. Open it
in a browser, then use `cu` to fill in the fields, tick the checkbox with
Tab/Space, click Preview and check the result in a screenshot. This has
been verified on a 1920×1080 X11 desktop with Firefox. Unicode typing was
tested in a Debian 13 VM (Xvfb, Openbox, xterm): accents, symbols, Greek,
Cyrillic, CJK, emoji, 300 distinct CJK characters, a German layout, Caps
Lock refusal, concurrent runs, and keymap recovery after SIGTERM and
SIGKILL. Clipboard reading was tested in the same VM against a scripted
selection owner (UTF-8, STRING-only, refusing, silent, and a 1.2 MB INCR
transfer) and against Firefox ESR: the devtools `copy(document.body.innerText)`
recipe on a 20,000-row page (553 KB) and Ctrl+A/Ctrl+C with both CLIPBOARD
and PRIMARY.

## AI usage disclaimer

This project was written almost entirely by AI coding agents. Anthropic's
Claude wrote the code, tests and documentation. OpenAI's GPT Sol reviewed
the changes over several rounds, and every finding was fixed. The unit
tests, a manual Firefox run on one X11 desktop and scripted xterm tests in
a VM are the only testing done. The code has not been audited line by line by a human.

Treat it as experimental software. Read the code before running it on a
machine that matters, especially since `cu` controls your real mouse,
keyboard and screen. See [Safety](#safety) and the [License](LICENSE)
(provided "as is", without warranty).

## License

[MIT](LICENSE)
