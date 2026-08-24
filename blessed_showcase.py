#!/usr/bin/env python3
"""A guided tour of the `blessed` terminal library.

blessed wraps terminfo(5) so you get colors, styles, cursor positioning,
and keyboard input without hand-rolling escape sequences yourself -- and
it degrades gracefully (no garbage codes) when output isn't a real tty.

Run it directly in a terminal -- piping it somewhere just gets you a
polite refusal, blessed is way too classy to spew escape codes at a file.

    python3 blessed_showcase.py

Pick a demo from the menu with its number, or 'q' to quit at any time.
Needs the `blessed` package -- missing it gets auto-installed on first run.
"""

import itertools
import os
import subprocess
import sys
import time

# ── dependency check ─────────────────────────────────────────────────────────
# A fresh checkout on a new machine won't have blessed installed yet, so
# check first and pip-install it instead of just dying on the import with
# a cryptic traceback. Checking importability isn't free -- especially over
# a slow/networked path like a WSL UNC share -- so once confirmed we drop a
# flag file next to the script and skip the check on later runs.

_DEPS_FLAG = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.blessed_showcase_deps_ok')


def _is_importable(name):
    try:
        __import__(name)
        return True
    except ImportError:
        return False


def _ensure_dependencies():
    if os.path.exists(_DEPS_FLAG):
        return  # verified on a previous run -- trust it and skip

    if not _is_importable('blessed'):
        print("Missing dependency: blessed -- installing...")
        base_cmd = [sys.executable, '-m', 'pip', 'install']
        result = subprocess.run(base_cmd + ['blessed'])
        if result.returncode != 0:
            # Some distros (Debian/Ubuntu's PEP 668 "externally managed"
            # guard) refuse a bare system-wide install and want --user
            # instead; give that one retry before giving up.
            result = subprocess.run(base_cmd + ['--user', 'blessed'])
        if result.returncode != 0 or not _is_importable('blessed'):
            print("\nAutomatic install didn't work. Please install manually:\n"
                  f"    {sys.executable} -m pip install blessed")
            sys.exit(1)

    try:
        open(_DEPS_FLAG, 'w', encoding='utf-8').close()
    except OSError:
        pass  # no write access next to the script -- fine, just re-check next time


_ensure_dependencies()

from blessed import Terminal

term = Terminal()


def wait_for_key(prompt='Press any key to continue...'):
    print(term.move_x(0) + term.dim(prompt))
    with term.cbreak():
        return term.inkey()


def demo_styles():
    """Text styles, named colors, and (if supported) a truecolor ramp."""
    print(term.clear + term.home)
    print(term.bold_underline_white_on_blue(term.center(' Styles & Colors ')))
    print()

    styles = [
        ('bold', term.bold),
        ('dim', term.dim),
        ('italic', term.italic),
        ('underline', term.underline),
        ('reverse', term.reverse),
        ('strikethrough', term.strikethrough),
    ]
    for name, style in styles:
        print(f'{name:>14s}: {style("The quick brown fox jumps over the lazy dog")}')

    print('\n  Named colors: ', end='')
    for color in ('red', 'green', 'yellow', 'blue', 'magenta', 'cyan'):
        print(getattr(term, color)(f' {color} '), end='')
    print()

    if term.number_of_colors >= 256:
        print('\n  24-bit color ramp (term.color_rgb):')
        ramp = ''.join(term.color_rgb(i * 4, 255 - i * 4, 128)('#') for i in range(64))
        print(f'  {ramp}')
    else:
        print("\n  (Terminal reports fewer than 256 colors -- skipping the truecolor ramp.)")

    print()
    wait_for_key()


def demo_positioning():
    """Absolute cursor placement with move_xy, plus width/height awareness."""
    with term.fullscreen(), term.hidden_cursor():
        print(term.clear, end='')
        print(term.move_xy(0, 0) + term.underline('Cursor positioning demo'), end='')
        print(term.move_xy(2, 2) + 'top-left-ish', end='')
        print(term.move_xy(term.width - 15, 4) + 'top-right-ish', end='')
        print(term.move_xy(2, term.height - 3) + 'bottom-left-ish', end='')
        centered = term.reverse(' dead center ')
        print(term.move_xy(term.width // 2 - len(' dead center ') // 2, term.height // 2) + centered, end='')
        footer = f'(terminal size: {term.width}x{term.height})'
        print(term.move_xy(0, term.height - 1) + term.dim(footer), end='', flush=True)
        wait_for_key()


def demo_keyboard():
    """Live key echo, showing how blessed decodes single keys and escape sequences alike."""
    print(term.clear + term.home)
    print(term.bold('Keyboard demo') + ' -- press a few keys (try arrows!), q to stop.\n')
    with term.cbreak():
        while True:
            key = term.inkey()
            if key == 'q':
                break
            label = key.name if key.is_sequence else repr(str(key))
            print(f'  code={key.code!r:>8}  is_sequence={key.is_sequence!s:<5}  -> {label}')


def demo_progress_bar():
    """A classic animated progress bar -- every CLI tool's favorite party trick."""
    print(term.clear + term.home)
    print(term.bold('Progress bar demo') + ' (plays automatically)\n')
    width = max(10, min(50, term.width - 20))
    with term.hidden_cursor():
        for pct in range(0, 101, 2):
            filled = int(width * pct / 100)
            bar = '[' + '#' * filled + '-' * (width - filled) + ']'
            print(term.move_x(0) + f'{bar} {pct:3d}%' + term.clear_eol, end='', flush=True)
            time.sleep(0.03)
    print()
    wait_for_key()


def demo_bounce():
    """A bouncing ball animation -- the 'hello world' of terminal graphics."""
    colors = itertools.cycle([term.red, term.yellow, term.green, term.cyan, term.blue, term.magenta])
    with term.fullscreen(), term.hidden_cursor(), term.cbreak():
        x, y, dx, dy = 2, 2, 1, 1
        deadline = time.time() + 8
        while time.time() < deadline:
            w, h = term.width, term.height
            print(term.clear + term.move_xy(x, y) + next(colors)('o'), end='')
            print(term.move_xy(0, h - 1) + term.dim('Bouncing for a few seconds -- press q to skip'),
                  end='', flush=True)

            x, y = x + dx, y + dy
            if x <= 0 or x >= w - 1:
                dx *= -1
            if y <= 0 or y >= h - 1:
                dy *= -1

            if term.inkey(timeout=0.05) == 'q':
                break


MENU = [
    ('1', 'Styles & colors', demo_styles),
    ('2', 'Cursor positioning', demo_positioning),
    ('3', 'Keyboard input', demo_keyboard),
    ('4', 'Progress bar', demo_progress_bar),
    ('5', 'Bouncing ball animation', demo_bounce),
]


def main():
    if not term.is_a_tty:
        print("This demo needs a real terminal -- run it directly, don't pipe or redirect it.")
        return

    while True:
        print(term.clear + term.home)
        print(term.bold_white_on_blue(term.center(' blessed showcase ')))
        print()
        for key, label, _ in MENU:
            print(f'  [{key}] {label}')
        print('  [q] Quit')
        print()

        with term.cbreak():
            choice = term.inkey()

        if choice == 'q':
            break

        for key, _, func in MENU:
            if choice == key:
                func()
                break


if __name__ == '__main__':
    main()
