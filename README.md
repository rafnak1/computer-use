<img src="assets/icon.svg" width="64" height="64" alt="">

# computer-use

`cu` is a small command-line tool that lets an AI agent, or a script, drive
the current X11 desktop. It takes screenshots, reports the focused window,
moves and clicks the mouse, types text, presses keys, scrolls, reads
copied text from the clipboard and waits for the window title, the active
window, the screen or the clipboard to change.
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
  selection; a browser "open tab" shortcut; timed waits and waits for a
  title, window, screen or clipboard condition; and JSON batches
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
- **No real load or success detection.** Exit code 0 means the input was
  sent, not that the app reacted. [Condition waits](#waiting-for-a-condition)
  watch indirect signals: the window title (usually set once a page has
  loaded), the active window, the screen settling, or the clipboard. A
  title can change before the page finishes drawing, and a page that
  keeps animating never settles, so take a screenshot to confirm.
- **Keyboard layout and Unicode.** `type` types characters found on the
  plain or Shift level of the first keyboard layout directly. Any other
  character (accents, AltGr symbols, other scripts, emoji) is typed by
  briefly binding it to an unused keycode, pressing that key, and then
  resetting the keycode, the same technique xdotool uses. This changes the
  shared X keymap for a moment. A keymap lock, separate from the
  [input lock](#one-process-at-a-time), keeps two `cu` processes from
  clashing, and if a run is killed the next run restores what it left. Keycodes that another program changed in the meantime are
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
- **`open-tab` is a shortcut, not an API.** It presses Ctrl+T in the
  focused window, types the URL and presses Enter. It refuses with
  `unexpected_window` unless that window is a known browser and not a
  dialog (see [Checking the window first](#checking-the-window-first)), but
  it cannot tell whether the tab opened.
- **Input is exclusive only among `cu` processes** of one user on one
  display, one command or batch at a time. **Window checks** look at the
  active window just before the input. See
  [One process at a time](#one-process-at-a-time) and
  [Checking the window first](#checking-the-window-first) for what they miss.
- **Clipboard: read-only, text only.** `cu clipboard` reads text that an
  app has already copied. It cannot set the clipboard, copy for you or read
  images and files (`unsupported_target`). It asks for `UTF8_STRING`, then
  `text/plain;charset=utf-8`, then `STRING`, and supports incremental (INCR)
  transfers for large text, up to 64 MiB. It fails with `clipboard_empty`
  when no app owns the selection and `clipboard_timeout` when the owner stays
  silent for 3 seconds. Apps take the clipboard asynchronously; use
  `cu key ctrl+c --wait-for clipboard-change`, since until they do you
  read the previous contents. If the app that copied has exited, the clipboard is usually
  gone too, unless a clipboard manager keeps it.
- **One screen, full captures.** Screenshots always cover the whole root
  window. There is no per-window or region capture, and with several
  monitors you get a single combined image.
- **Synthetic input.** Events go through the XTEST extension. Some apps,
  games or security prompts may ignore or treat that input differently.
- **Limits:** wait and wait timeouts 0–60 s, scroll 1–50 ticks per call, click count 1–5,
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
cu wait --title-change | --title-contains TEXT | --title-matches REGEX | --window-change
        | --stable [SECONDS] | --clipboard-change [--timeout SECONDS]
cu key Return --wait-for title-change       # any input action + a condition
cu clipboard [--selection clipboard|primary] [--out FILE]
cu batch actions.json [--screenshot /tmp/final.png]   # "-" reads stdin
cu key ctrl+w --expect-title TEXT --expect-class CLASS   # any input action, or batch
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

### One process at a time

`key`, `type`, `click`, `move`, `scroll` and `open-tab` take an input lock
for your user and display, so two `cu` processes cannot mix their keys and
clicks. A single command holds it until it ends, including `--wait-for`. A
`batch` with at least one of these actions holds it from before its first
action through its waits and final screenshot. Arguments are checked before locking,
and `--wait-for` baselines are recorded after it. `info`, `observe`, `screenshot`,
`clipboard`, `wait` and batches made only of those never take it.

If another process holds the lock, `cu` waits up to 5 s, then exits 1
without sending anything:

```json
{"ok": false, "error": {"code": "busy", "message": "...", "hint": "...", "holder_pid": 4242}}
```

The lock is an `flock` on `cu-input-UID-DISPLAY.lock` in `$XDG_RUNTIME_DIR`
(or the temp directory), so a killed process frees it. The PID in the file is only informative:
it may be `null` or belong to an earlier holder. `input_lock` means the file
cannot be used (not a private regular file of yours, or no such directory).

What the lock does not cover:

- Separate commands from two processes can still alternate. Put a sequence
  that must not be interrupted in one `batch`.
- A person at the keyboard, other users and tools other than `cu`.
- `cu` versions without the lock, and processes with a different
  `$XDG_RUNTIME_DIR`, temp directory or spelling of the display name
  (`:0` and `:0.0` count as the same).

### Checking the window first

`--expect-title TEXT` and `--expect-class CLASS` make an input command fail
instead of acting on the wrong window. The title must contain TEXT, as with
`title-contains`. The WM class or instance must contain CLASS, ignoring
case: snap Firefox has class `firefox_firefox` and instance `Navigator`.
If both are given, both must match, and no active window is a mismatch. The
check runs under the input lock, right before the first event. On a mismatch
`cu` exits 1 and sends nothing:

```sh
cu type "hello" --expect-class Navigator --expect-title "Upload form"
```

```json
{"ok": false, "error": {"code": "unexpected_window", "message": "...", "hint": "...",
 "expected": {"title": "Upload form", "class": "Navigator"},
 "active_window": {"id": "0x60001e", "title": "File Upload - Upload form — Mozilla Firefox",
  "instance": "firefox-esr", "class": "firefox-esr", "type": ["dialog"], "transient_for": "0x600003"}}}
```

`active_window` is `null` when there is none. On `batch` the options apply
before every input action. Items can set their own `expect_title` and
`expect_class`, and `null` turns one check off for that item, which steps
inside a dialog need. A mismatch stops the batch with `failed_index`:

```json
[
  {"action": "click", "x": 47, "y": 502, "wait_for": "window-change"},
  {"action": "wait", "seconds": 1},
  {"action": "type", "text": "/tmp/report.pdf", "expect_class": null, "expect_title": "File Upload"},
  {"action": "wait", "seconds": 1},
  {"action": "key", "keys": "Return", "expect_class": null, "expect_title": "File Upload"},
  {"action": "wait", "seconds": 1},
  {"action": "click", "x": 285, "y": 240},
  {"action": "type", "text": "example"}
]
```

```sh
cu batch upload.json --expect-class Navigator   # stops if the picker is still open
```

(The waits are for GTK's file picker, which may ignore Return right after
the path is typed.)

Choose a value that only the right window has. A browser's dialogs share
its class, and Firefox's dialogs also repeat the page title ("File Upload -
Upload form — Mozilla Firefox"), so neither `--expect-class firefox` nor the
page title stops at them. In Firefox use `--expect-class Navigator`, the
instance of its main windows only. In Chromium, whose dialogs have the
instance of the main window, use the title. `cu info` shows all of these,
plus `type` and `transient_for`, which mark dialogs. Titles also change
when a page loads, so they suit single steps better than whole batches.

`open-tab` always checks that the active window is a known browser (the
Firefox and Chromium families) and not a dialog, since a browser's file
picker has the browser's class. An explicit `--expect-class` (also as a
batch default) replaces the browser list, so other browsers work; `null`
goes back to the list. Dialogs, recognised by `_NET_WM_WINDOW_TYPE` or
`WM_TRANSIENT_FOR`, are refused either way.

What the checks do not cover:

- They look at the active window, while a click or scroll goes to whatever
  is under the pointer.
- Focus can change after the check, for example during a long `type` or
  between the Ctrl+T and the URL of `open-tab`.
- Without an expectation, commands other than `open-tab` act on any window.
- `open-tab` does not recognise every non-tab window of a browser, such as
  Firefox's Library, which is neither a dialog nor of another class.

### Waiting for a condition

Instead of guessing a sleep, wait until something observable happens.
`cu wait` polls every 0.1 s until its condition is met or `--timeout`
(default 10 s, max 60 s) runs out:

| Condition | Done when |
|---|---|
| `--title-change` | the active window's title differs from the starting title |
| `--title-contains TEXT`, `--title-matches REGEX` | the active window's title contains TEXT, or `re.search` finds REGEX |
| `--window-change` | another window is active (a dialog, a new window, or none) |
| `--stable [SECONDS]` | the screen has not changed for SECONDS (default 0.5) |
| `--clipboard-change` | the clipboard (or `--selection primary`) has a new owner, a new TIMESTAMP or new text |

```json
{"ok": true, "action": "wait", "condition": "title-change", "elapsed": 1.84, "polls": 17,
 "title": "Example Domain — Mozilla Firefox", "window": "0x400003", "from": "Mozilla Firefox", "met": true}
```

On timeout `cu` exits 1 with error code `wait_timeout`, plus `elapsed`,
`polls` and the last value seen, so the agent can decide what to do next.

A "change" condition needs a starting value. A separate `cu wait
--title-change` starts from the title when it begins, so it misses a
change that already happened. To avoid that race, attach the condition to
the action with `--wait-for` (`open-tab`, `key`, `type`, `click`, `move` and
`scroll` take it, along with `--timeout` and `--tolerance`). The starting value is
recorded before the action runs, or for `open-tab` after the new tab has
opened and just before Enter:

```sh
cu open-tab https://example.com --wait-for title-change
cu key Return --wait-for title-change           # submit a search, next listing
cu key ctrl+s --wait-for window-change          # "Save As" dialog appeared
cu key ctrl+c --wait-for clipboard-change       # the app took the clipboard
cu scroll down 5 --wait-for stable              # smooth scrolling has stopped
```

`--wait-for` takes `title-change`, `title-contains=TEXT`,
`title-matches=REGEX`, `window-change`, `stable[=SECONDS]` and
`clipboard-change[=primary]`. The action's result gets a `wait` object; if
the wait times out, the error carries the action's result as
`action_result`. `cu wait --title-change --from "Old title"` (or
`--window-change --from 0x1a00003`) sets the starting value explicitly.

Notes:

- The title and window come from `xprop`, which runs twice per poll.
- `--stable` compares raw `xwd` captures without encoding PNGs. Spinners,
  video, animations and blinking text cursors stop the screen from
  settling; `--tolerance PIXELS` ignores up to that many changed pixels
  (compared with the first frame of the quiet period, so slow drift still
  counts). Each poll captures the whole screen, so on large desktops polls
  are further apart than 0.1 s.
- `--clipboard-change` asks the owner for the ICCCM `TIMESTAMP` target, so
  copying the same text again still counts as a change. With an owner that
  does not answer `TIMESTAMP`, only a new owner or new text counts.

### Reading text from the screen

Copying and reading the clipboard is usually faster and more accurate than
scrolling through screenshots:

```sh
cu key ctrl+a; cu key ctrl+c --wait-for clipboard-change
cu clipboard --out /tmp/page.txt
```

For the whole text of a web page in Firefox, use the devtools console
(Ctrl+Shift+J in Chrome):

```sh
cu key ctrl+shift+k; cu wait 1
cu type 'copy(document.body.innerText)'
cu key Return --wait-for clipboard-change
cu key F12                        # close devtools again
cu clipboard --out /tmp/page.txt
```

Keys go wherever focus is, so add
[`--expect-class` or `--expect-title`](#checking-the-window-first).

### Batch

A batch is a JSON array (or `{"actions": [...]}`) of these actions: `info`;
`observe`/`screenshot` with optional `path`; `open-tab` with `url`; `key`
with `keys`; `type` with `text`; `click` with `x`, `y`, optional `button`,
`count` and `hover` (seconds); `move` with `x`, `y`; `scroll` with `direction` and optional
`amount`; `wait` with `seconds`, or with `until` (a condition as above)
and optional `timeout`, `tolerance` and `from`; `clipboard` with optional
`selection` (`clipboard` or `primary`) and `out`. `open-tab`, `key`, `type`,
`click`, `move` and `scroll` also take optional `wait_for`, `timeout`,
`tolerance`, `expect_title` and `expect_class`.

In a batch, a `wait` with a "change" condition starts from the value
recorded just before the previous action (for `open-tab`, just before
Enter), so a change that action caused still counts even if it happened
before the wait began. A change that reverts before the first poll (a
dialog that opens and closes at once) is still missed:

```json
[
  {"action": "click", "x": 640, "y": 410},
  {"action": "wait", "until": "title-change", "timeout": 15},
  {"action": "observe", "path": "/tmp/next.png"}
]
```

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
  {"action": "key", "keys": "ctrl+c", "wait_for": "clipboard-change"},
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
release of held mouse buttons, the input lock (with real lock files and a
second process), window expectations, the Unicode typing plan and keymap
restore logic (against an in-memory fake keymap), and clipboard reading
(target fallback, INCR assembly, timeouts and size limits against a
scripted fake selection owner), and condition waits (argument checks,
baselines before the action and in batches, timeouts, and the title,
window, frame and clipboard comparisons against fake sources and a fake
clock). One test that resolves key names is
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
and PRIMARY. Condition waits were tested in the same VM with Openbox and
Firefox ESR: `open-tab --wait-for title-change`, `--title-contains` on a
page that sets its title after 2 s, the Save As dialog with
`--window-change` (single commands and a batch), `--stable` on a static
and an animated page (timeout, then met with `--tolerance`), and
`--clipboard-change` after Ctrl+C, including copying the same text again.
The input lock and window checks were tested in a Debian 13 container (Xvfb,
xfwm4, xfdesktop): overlapping `batch` loops in Firefox ESR (12 of 12 URLs
intact, 1 of 12 before the lock), `busy` for every input command, read-only
commands while the lock is held, SIGKILL of the holder, every input command
with matching and mismatching expectations, batches stopping at an open file
picker, and `open-tab` with Firefox ESR, Chromium, their file pickers,
xfdesktop and xterm. Snap and Flatpak browsers were not tested.

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
