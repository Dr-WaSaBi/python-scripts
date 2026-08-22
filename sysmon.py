#!/usr/bin/env python3
"""sysmon.py — an htop/ntop-style live dashboard, built on `blessed`.

CPU load per core, memory/swap, network throughput per interface, disk
usage, and a sortable process table -- all in one full-screen view that
redraws in place (no flicker, no full-screen clears) and re-flows itself
automatically if you resize the terminal, because blessed's term.width
and term.height are just live properties, not something you snapshot
once and hope stays true. 📡

Requires psutil (`pip install psutil`) for the system/process data;
blessed does all the terminal rendering, styling, and input.

    python3 sysmon.py [--refresh SECONDS]

Keys while running:
    c        sort processes by CPU%
    m        sort processes by memory%
    p        sort processes by PID
    + / -    speed up / slow down the refresh rate
    q        quit
"""

import argparse
import platform
import time

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


def bar(pct, width):
    """A blessed-colored usage bar, green -> yellow -> red as pct climbs."""
    pct = max(0.0, min(100.0, pct))
    filled = int(width * pct / 100)
    color = term.green if pct < 70 else term.yellow if pct < 90 else term.red
    return color('█' * filled) + term.dim('░' * (width - filled))


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
                        'user': safe(proc.username, '?')[:10],
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
        load1, load5, load15 = (x for x in __import__('os').getloadavg())
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


def build_disk():
    lines = [term.bold('Disks')]
    seen_mounts = set()
    width = min(40, term.width - 34)
    for part in psutil.disk_partitions():
        if not part.device.startswith('/dev/') or part.mountpoint in seen_mounts:
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


def build_procs(rows, sort_key, max_rows):
    keyfunc = {'cpu': lambda r: -r['cpu'], 'mem': lambda r: -r['mem'], 'pid': lambda r: r['pid']}[sort_key]
    rows = sorted(rows, key=keyfunc)

    fixed_w = 6 + 1 + 10 + 1 + 7 + 1 + 7 + 1 + 1 + 1
    name_w = max(10, term.width - fixed_w)
    header = f'{"PID":>6} {"USER":<10} {"CPU%":>7} {"MEM%":>7} S {"COMMAND":<{name_w}}'
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
    return lines


def build_footer(sort_key, refresh):
    hint = (f"[q]uit  [c]pu-sort  [m]em-sort  [p]id-sort  [+/-]speed"
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

    with term.fullscreen(), term.hidden_cursor(), term.cbreak():
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
            lines += build_procs(proc_rows, sort_key, remaining)
            lines += build_footer(sort_key, refresh)

            render(lines)

            key = term.inkey(timeout=refresh)
            if key == 'q':
                break
            elif key.lower() == 'c':
                sort_key = 'cpu'
            elif key.lower() == 'm':
                sort_key = 'mem'
            elif key.lower() == 'p':
                sort_key = 'pid'
            elif key in ('+', '='):
                refresh = max(0.2, refresh - 0.2)
            elif key == '-':
                refresh = min(5.0, refresh + 0.2)


if __name__ == '__main__':
    main()
