#!/usr/bin/env python3
"""sysmon.py — an htop/ntop-style live dashboard, built on `blessed`.

CPU load per core, memory/swap, network throughput per interface, disk
usage, and a sortable process table -- all in one full-screen view that
redraws in place (no flicker, no full-screen clears) and re-flows itself
automatically if you resize the terminal, because blessed's term.width
and term.height are just live properties, not something you snapshot
once and hope stays true. 📡

Needs psutil and blessed -- missing either one gets auto-installed on
first run, so a fresh checkout just works. Runs on Linux, macOS, and
Windows 10+ (Windows Terminal recommended for full color/mouse support).

    python3 sysmon.py [--refresh SECONDS]      (Linux/macOS)
    python sysmon.py [--refresh SECONDS]       (Windows)

Keys while running:
    c / m / p / u / n   sort processes by CPU% / memory% / PID / user / command
    (or just click a column header in the process table -- same effect)
    + / -               speed up / slow down the refresh rate
    q                   quit
"""

import argparse
import os
import platform
import subprocess
import sys
import time

# ── dependency check ─────────────────────────────────────────────────────────
# A fresh checkout on a new machine (looking at you, Windows) won't have
# these installed yet, so check first and pip-install anything missing
# instead of just dying on the import with a cryptic traceback. Checking
# importability isn't free -- especially over a slow/networked path like a
# WSL UNC share -- so once everything's confirmed present we drop a flag
# file next to the script and skip the check entirely on later runs. The
# flag records *which* packages it verified, so adding a new required
# package later automatically invalidates a stale flag instead of silently
# skipping the check for something that was never actually confirmed.

REQUIRED_PACKAGES = ('psutil', 'blessed')
_DEPS_FLAG = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.sysmon_deps_ok')


def _is_importable(name):
    try:
        __import__(name)
        return True
    except ImportError:
        return False


def _ensure_dependencies():
    flag_contents = ','.join(REQUIRED_PACKAGES)
    try:
        with open(_DEPS_FLAG, encoding='utf-8') as f:
            if f.read().strip() == flag_contents:
                return  # verified on a previous run -- trust it and skip
    except OSError:
        pass

    missing = [pkg for pkg in REQUIRED_PACKAGES if not _is_importable(pkg)]
    if missing:
        print(f"Missing dependencies: {', '.join(missing)} -- installing...")
        base_cmd = [sys.executable, '-m', 'pip', 'install']
        result = subprocess.run(base_cmd + missing)
        if result.returncode != 0:
            # Some distros (Debian/Ubuntu's PEP 668 "externally managed"
            # guard) refuse a bare system-wide install and want --user
            # instead; give that one retry before giving up.
            result = subprocess.run(base_cmd + ['--user'] + missing)

        if result.returncode != 0 or any(not _is_importable(pkg) for pkg in missing):
            print("\nAutomatic install didn't work. Please install manually:\n"
                  f"    {sys.executable} -m pip install {' '.join(missing)}")
            sys.exit(1)

    try:
        with open(_DEPS_FLAG, 'w', encoding='utf-8') as f:
            f.write(flag_contents)
    except OSError:
        pass  # no write access next to the script -- fine, just re-check next time


_ensure_dependencies()

import psutil
from blessed import Terminal

term = Terminal()


# ── formatting helpers ───────────────────────────────────────────────────────

def fmt_bytes(n):
    n = float(n)
    for unit in ('B', 'K', 'M', 'G', 'T'):
        if abs(n) < 1024:
            return f'{n:5.1f}{unit}'
        n /= 1024
    return f'{n:5.1f}P'


def fmt_rate(bytes_per_sec):
    return fmt_bytes(bytes_per_sec) + '/s'


def fmt_uptime(seconds):
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    mins, _ = divmod(rem, 60)
    if days:
        return f'{days}d {hours:02d}h {mins:02d}m'
    return f'{hours:02d}h {mins:02d}m'


def safe(func, default='?'):
    """Run a psutil call that loves to raise AccessDenied/NoSuchProcess/etc."""
    try:
        return func()
    except (psutil.Error, OSError):
        return default


def short_username(name):
    """Windows returns 'DOMAIN\\user' -- keep just the account name so it
    fits the column; a no-op on POSIX names (no backslash present)."""
    return name.rsplit('\\', 1)[-1]


# eighths of a block, for sub-character bar resolution -- 1/8 through 7/8
# (8/8 is just a plain full block, no glyph needed for that one)
EIGHTHS = ('▏', '▎', '▍', '▌', '▋', '▊', '▉')


def bar(pct, width):
    """A blessed-colored usage bar, green -> yellow -> red as pct climbs.

    Real-world load is usually either near-idle or near-maxed, and a
    plain linear 0-100 scale makes anything under ~10% vanish into an
    all-empty bar on a modest-width meter. So the *fill* uses a sqrt
    curve to stretch out the low end (100% still fills it completely),
    while the color thresholds and the printed percentage stay tied to
    the real, unscaled value -- only the bar's shape is exaggerated,
    never the number next to it.
    """
    pct = max(0.0, min(100.0, pct))
    fraction = (pct / 100) ** 0.5
    eighths_total = round(width * 8 * fraction)
    full, remainder = divmod(eighths_total, 8)
    full = min(full, width)
    filled = '█' * full
    if remainder and full < width:
        filled += EIGHTHS[remainder - 1]
        full += 1
    color = term.green if pct < 70 else term.yellow if pct < 90 else term.red
    return color(filled) + term.dim('░' * (width - full))


# ── data collection ──────────────────────────────────────────────────────────

class ProcTracker:
    """Keeps psutil.Process handles alive across frames so cpu_percent()
    can report a real delta instead of the meaningless 0.0 you get from
    a brand new Process object (that's just how psutil's API works)."""

    def __init__(self):
        self.cache = {}

    def snapshot(self):
        rows = []
        live_pids = set(psutil.pids())
        for pid in list(self.cache):
            if pid not in live_pids:
                del self.cache[pid]

        for pid in live_pids:
            proc = self.cache.get(pid)
            freshly_seen = proc is None
            if freshly_seen:
                try:
                    proc = psutil.Process(pid)
                    proc.cpu_percent(None)  # prime the pump, ignore result
                except psutil.Error:
                    continue
                self.cache[pid] = proc
            try:
                with proc.oneshot():
                    cpu = 0.0 if freshly_seen else proc.cpu_percent(None)
                    rows.append({
                        'pid': pid,
                        'user': short_username(safe(proc.username, '?'))[:10],
                        'cpu': cpu,
                        'mem': safe(proc.memory_percent, 0.0),
                        'status': safe(proc.status, '?')[:1].upper(),
                        'name': safe(proc.name, '?'),
                    })
            except psutil.Error:
                continue
        return rows


# ── frame builders (each returns a list of already-styled lines) ───────────

def build_header():
    uname = platform.uname()
    uptime = fmt_uptime(time.time() - psutil.boot_time())
    now = time.strftime('%Y-%m-%d %H:%M:%S')
    try:
        # psutil emulates getloadavg() on platforms without a native one
        # (Windows, macOS) via a background sample, so this works cross-
        # platform instead of just falling back to 'n/a' off of Linux.
        load1, load5, load15 = psutil.getloadavg()
        load = f'{load1:.2f} {load5:.2f} {load15:.2f}'
    except (AttributeError, OSError):
        load = 'n/a'

    title = f' {uname.node} '
    return [
        term.bold_black_on_green(term.center(title)),
        (f'  {uname.system} {uname.release}   uptime {uptime}   '
         f'load {load}   {now}'),
        '',
    ]


def build_cpu(percpu):
    lines = [term.bold('CPU')]
    overall = sum(percpu) / len(percpu) if percpu else 0.0
    lines.append('  ' + f'{"all":>4} ' + bar(overall, min(50, term.width - 12)) + f' {overall:5.1f}%')

    col_width = 22
    cols = max(1, term.width // col_width)
    core_bar_w = 10
    row = []
    for i, pct in enumerate(percpu):
        cell = f'{i:>3} ' + bar(pct, core_bar_w) + f' {pct:5.1f}%'
        row.append(cell)
        if len(row) == cols:
            lines.append('  ' + '  '.join(row))
            row = []
    if row:
        lines.append('  ' + '  '.join(row))
    lines.append('')
    return lines


def build_mem():
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    width = min(50, term.width - 24)
    lines = [term.bold('Memory')]
    lines.append('  ' + 'RAM  ' + bar(vm.percent, width) +
                 f' {vm.percent:5.1f}%  {fmt_bytes(vm.used)}/{fmt_bytes(vm.total)}')
    lines.append('  ' + 'Swap ' + bar(sw.percent, width) +
                 f' {sw.percent:5.1f}%  {fmt_bytes(sw.used)}/{fmt_bytes(sw.total)}')
    lines.append('')
    return lines


def build_net(rates):
    lines = [term.bold('Network')]
    if not rates:
        lines.append('  (gathering samples...)')
    for name, (down, up, total_down, total_up) in sorted(rates.items()):
        lines.append(
            f'  {name:<10} '
            f'{term.cyan("v")} {fmt_rate(down):>10}   '
            f'{term.magenta("^")} {fmt_rate(up):>10}   '
            + term.dim(f'(total v{fmt_bytes(total_down)} ^{fmt_bytes(total_up)})')
        )
    lines.append('')
    return lines


def _is_local_disk(part):
    """psutil already excludes empty-media/virtual drives on Windows via
    disk_partitions(all=False), so accept everything there; on POSIX we
    still want to filter out virtual/pseudo mounts (tmpfs, overlay, etc.)
    that don't correspond to a real block device."""
    if os.name != 'posix':
        return True
    return part.device.startswith('/dev/')


def build_disk():
    lines = [term.bold('Disks')]
    seen_mounts = set()
    width = min(40, term.width - 34)
    for part in psutil.disk_partitions():
        if not _is_local_disk(part) or part.mountpoint in seen_mounts:
            continue
        seen_mounts.add(part.mountpoint)
        usage = safe(lambda: psutil.disk_usage(part.mountpoint), None)
        if usage is None:
            continue
        label = part.mountpoint if len(part.mountpoint) <= 18 else part.mountpoint[:15] + '...'
        lines.append('  ' + f'{label:<18} ' + bar(usage.percent, width) +
                     f' {usage.percent:5.1f}%  {fmt_bytes(usage.used)}/{fmt_bytes(usage.total)}')
    lines.append('')
    return lines


# sort key -> comparator; also doubles as the whitelist of clickable/sortable columns
SORT_SPECS = {
    'pid':  lambda r: r['pid'],
    'user': lambda r: r['user'].lower(),
    'cpu':  lambda r: -r['cpu'],
    'mem':  lambda r: -r['mem'],
    'name': lambda r: r['name'].lower(),
}


def build_procs(rows, sort_key, max_rows):
    rows = sorted(rows, key=SORT_SPECS[sort_key])

    fixed_w = 6 + 1 + 10 + 1 + 7 + 1 + 7 + 1 + 1 + 1
    name_w = max(10, term.width - fixed_w)

    # (sort key or None, title, width, alignment) -- one source of truth for
    # both the header text and the x-ranges mouse clicks are tested against,
    # so the two can never drift out of sync.
    columns = [
        ('pid', 'PID', 6, '>'),
        ('user', 'USER', 10, '<'),
        ('cpu', 'CPU%', 7, '>'),
        ('mem', 'MEM%', 7, '>'),
        (None, 'S', 1, '<'),
        ('name', 'COMMAND', name_w, '<'),
    ]

    header_cells, col_ranges, x = [], {}, 0
    for key, title, width, align in columns:
        header_cells.append(f'{title:{align}{width}}')
        if key:
            col_ranges[key] = (x, x + width)
        x += width + 1  # +1 for the single-space separator between columns

    header = ' '.join(header_cells)
    lines = [term.bold('Processes') + term.dim(f'  ({len(rows)} total, sorted by {sort_key})'),
             term.reverse(header[:term.width])]

    shown = rows[:max_rows]
    for r in shown:
        name = r['name'][:name_w]
        cpu_str = term.yellow(f"{r['cpu']:7.1f}") if r['cpu'] > 50 else f"{r['cpu']:7.1f}"
        line = f"{r['pid']:>6} {r['user']:<10} {cpu_str} {r['mem']:7.1f} {r['status']:<1} {name:<{name_w}}"
        lines.append(line[:term.width])

    hidden = len(rows) - len(shown)
    if hidden > 0:
        lines.append(term.dim(f'  ... and {hidden} more not shown (shrink the process list or grow your terminal)'))
    return lines, col_ranges


def build_footer(sort_key, refresh):
    hint = (f"[q]uit  click/[c/m/p/u/n] a column to sort  [+/-]speed"
            f"   sort={sort_key}  refresh={refresh:.1f}s")
    return [term.reverse(hint[:term.width].ljust(term.width))]


def render(lines):
    max_lines = term.height
    chunks = [term.home]
    for i, line in enumerate(lines[:max_lines]):
        chunks.append(str(line) + term.clear_eol)
        if i < min(len(lines), max_lines) - 1:
            chunks.append('\r\n')
    chunks.append(term.clear_eos)
    print(''.join(chunks), end='', flush=True)


# ── main loop ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--refresh', type=float, default=1.5, help='refresh interval in seconds (default: 1.5)')
    args = parser.parse_args()

    if not term.is_a_tty:
        print("sysmon needs a real terminal -- run it directly, don't pipe or redirect it.")
        return

    refresh = args.refresh
    sort_key = 'cpu'
    tracker = ProcTracker()

    psutil.cpu_percent(percpu=True)  # prime the global counters too
    prev_net, prev_time = None, None

    # mouse_enabled() does a one-time terminal capability query (same cost
    # does_mouse() would pay), so just enable it directly -- on a terminal
    # that doesn't support mouse reporting this is a harmless no-op and the
    # keyboard shortcuts still work.
    with term.fullscreen(), term.hidden_cursor(), term.cbreak(), term.mouse_enabled():
        while True:
            percpu = psutil.cpu_percent(percpu=True)

            cur_net = psutil.net_io_counters(pernic=True)
            cur_time = time.time()
            rates = {}
            if prev_net is not None:
                elapsed = max(1e-6, cur_time - prev_time)
                for name, counters in cur_net.items():
                    prev = prev_net.get(name)
                    if prev is None:
                        continue
                    down = (counters.bytes_recv - prev.bytes_recv) / elapsed
                    up = (counters.bytes_sent - prev.bytes_sent) / elapsed
                    rates[name] = (down, up, counters.bytes_recv, counters.bytes_sent)
            prev_net, prev_time = cur_net, cur_time

            proc_rows = tracker.snapshot()

            lines = []
            lines += build_header()
            lines += build_cpu(percpu)
            lines += build_mem()
            lines += build_net(rates)
            lines += build_disk()

            used_so_far = len(lines) + 3  # + process header/subheader + footer
            remaining = max(3, term.height - used_so_far)
            proc_section_y = len(lines)
            proc_lines, col_ranges = build_procs(proc_rows, sort_key, remaining)
            lines += proc_lines
            header_y = proc_section_y + 1  # the reversed column-header row
            lines += build_footer(sort_key, refresh)

            render(lines)

            key = term.inkey(timeout=refresh)
            if key.name == 'MOUSE_LEFT':
                click_y, click_x = key.mouse_yx
                if click_y == header_y:
                    for col_key, (x0, x1) in col_ranges.items():
                        if x0 <= click_x < x1:
                            sort_key = col_key
                            break
            elif key == 'q':
                break
            elif key.lower() in ('c', 'm', 'p', 'u', 'n'):
                sort_key = {'c': 'cpu', 'm': 'mem', 'p': 'pid', 'u': 'user', 'n': 'name'}[key.lower()]
            elif key in ('+', '='):
                refresh = max(0.2, refresh - 0.2)
            elif key == '-':
                refresh = min(5.0, refresh + 0.2)


if __name__ == '__main__':
    main()
