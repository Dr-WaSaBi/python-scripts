#!/usr/bin/env python3
"""
sysinfo.py — Cross-platform system information tool.

Shows hardware, uptime, and process load for the current environment.
When running inside WSL, also queries the Windows host via PowerShell.

Platforms: Linux, Windows, WSL1, WSL2
Optional:  pip install psutil   (richer info; falls back to /proc + ps without it)
"""

import json
import os
import platform
import re
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False


# ── Colour helpers (no deps) ─────────────────────────────────────────────────

SUPPORTS_COLOR = sys.stdout.isatty() or os.environ.get('FORCE_COLOR')

class C:
    RESET  = '\033[0m'  if SUPPORTS_COLOR else ''
    BOLD   = '\033[1m'  if SUPPORTS_COLOR else ''
    CYAN   = '\033[36m' if SUPPORTS_COLOR else ''
    GREEN  = '\033[32m' if SUPPORTS_COLOR else ''
    YELLOW = '\033[33m' if SUPPORTS_COLOR else ''
    RED    = '\033[31m' if SUPPORTS_COLOR else ''
    BLUE   = '\033[34m' if SUPPORTS_COLOR else ''
    WHITE  = '\033[97m' if SUPPORTS_COLOR else ''
    DIM    = '\033[2m'  if SUPPORTS_COLOR else ''


def section(title):
    bar = '─' * (len(title) + 4)
    print(f"\n{C.CYAN}{C.BOLD}┌{bar}┐")
    print(f"│  {title}  │")
    print(f"└{bar}┘{C.RESET}")


def kv(key, value, indent=2):
    pad = ' ' * indent
    print(f"{pad}{C.YELLOW}{key:<22}{C.RESET}{C.WHITE}{value}{C.RESET}")


def usage_bar(label, pct, width=28, indent=2):
    filled = int(width * pct / 100)
    color  = C.GREEN if pct < 70 else C.YELLOW if pct < 90 else C.RED
    bar    = '█' * filled + '░' * (width - filled)
    pad    = ' ' * indent
    print(f"{pad}{C.YELLOW}{label:<22}{C.RESET}{color}{bar}{C.RESET} {pct:5.1f}%")


# ── Formatting ───────────────────────────────────────────────────────────────

def fmt_bytes(n):
    n = int(n)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def fmt_uptime(seconds):
    td   = timedelta(seconds=int(seconds))
    days = td.days
    hrs, rem = divmod(td.seconds, 3600)
    mins, secs = divmod(rem, 60)
    parts = []
    if days:  parts.append(f"{days}d")
    if hrs:   parts.append(f"{hrs}h")
    if mins:  parts.append(f"{mins}m")
    parts.append(f"{secs}s")
    return ' '.join(parts)


# ── Platform detection ───────────────────────────────────────────────────────

def detect_env():
    """Return (system, is_wsl, wsl_version)."""
    system   = platform.system()   # 'Linux', 'Windows', 'Darwin'
    is_wsl   = False
    wsl_ver  = None

    if system == 'Linux':
        # Check /proc/version for Microsoft kernel signature
        try:
            version = Path('/proc/version').read_text().lower()
            if 'microsoft' in version:
                is_wsl  = True
                wsl_ver = 2 if 'wsl2' in version or 'microsoft-standard-wsl2' in version else 1
        except OSError:
            pass

        # Fallback: WSL sets this env var
        if not is_wsl and os.environ.get('WSL_DISTRO_NAME'):
            is_wsl  = True
            wsl_ver = 2

    return system, is_wsl, wsl_ver


# ── Linux info (via psutil or /proc) ─────────────────────────────────────────

def _proc_cpu():
    """Parse /proc/cpuinfo and return a dict of CPU facts."""
    info = {'model': 'unknown', 'physical': 0, 'logical': 0, 'mhz': 'unknown'}
    try:
        text = Path('/proc/cpuinfo').read_text()
        for line in text.splitlines():
            if ':' not in line:
                continue
            k, _, v = line.partition(':')
            k, v = k.strip(), v.strip()
            if k == 'model name' and info['model'] == 'unknown':
                info['model'] = v
            elif k == 'cpu MHz':
                info['mhz'] = f"{float(v):.0f} MHz"
        info['logical']  = text.count('processor\t:')
        phys_ids = {l.split(':')[1].strip() for l in text.splitlines() if l.startswith('physical id')}
        info['physical'] = len(phys_ids) if phys_ids else info['logical']
    except OSError:
        pass
    return info


def _proc_mem():
    """Parse /proc/meminfo and return values in bytes."""
    mem = {}
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if ':' not in line:
                continue
            k, _, v = line.partition(':')
            mem[k.strip()] = int(v.strip().split()[0]) * 1024
    except (OSError, ValueError):
        pass
    return mem


def _proc_uptime():
    try:
        return float(Path('/proc/uptime').read_text().split()[0])
    except OSError:
        return 0.0


def show_linux(label):
    section(label)

    # ── OS ──
    kv("OS",           f"{platform.system()} {platform.release()}")
    kv("Kernel",       platform.version()[:70])
    kv("Hostname",     platform.node())
    kv("Architecture", platform.machine())

    # ── CPU ──
    print(f"\n  {C.CYAN}CPU{C.RESET}")
    if HAS_PSUTIL:
        freq  = psutil.cpu_freq()
        speed = f"  @{freq.current:.0f} MHz" if freq else ""
        kv("Model", _proc_cpu()['model'])
        kv("Cores", f"{psutil.cpu_count(logical=False)} physical  /  "
                    f"{psutil.cpu_count()} logical{speed}")
        pcts = psutil.cpu_percent(interval=1, percpu=True)
        usage_bar("Usage (avg)", sum(pcts) / len(pcts))
        for i, p in enumerate(pcts):
            usage_bar(f"  Core {i}", p, width=20)
    else:
        info = _proc_cpu()
        kv("Model", info['model'])
        kv("Speed", info['mhz'])
        kv("Cores", f"{info['physical']} physical  /  {info['logical']} logical")
        try:
            load = os.getloadavg()
            kv("Load (1/5/15m)", f"{load[0]:.2f}  {load[1]:.2f}  {load[2]:.2f}")
        except OSError:
            pass

    # ── Memory ──
    print(f"\n  {C.CYAN}Memory{C.RESET}")
    if HAS_PSUTIL:
        m = psutil.virtual_memory()
        s = psutil.swap_memory()
        kv("RAM Total",  fmt_bytes(m.total))
        kv("RAM Used",   fmt_bytes(m.used))
        kv("RAM Free",   fmt_bytes(m.available))
        usage_bar("RAM", m.percent)
        if s.total:
            usage_bar("Swap", s.percent)
    else:
        m     = _proc_mem()
        total = m.get('MemTotal', 0)
        avail = m.get('MemAvailable', 0)
        used  = total - avail
        pct   = used / total * 100 if total else 0
        kv("RAM Total", fmt_bytes(total))
        kv("RAM Used",  fmt_bytes(used))
        kv("RAM Free",  fmt_bytes(avail))
        usage_bar("RAM", pct)
        swap_total = m.get('SwapTotal', 0)
        swap_free  = m.get('SwapFree', 0)
        if swap_total:
            swap_pct = (swap_total - swap_free) / swap_total * 100
            usage_bar("Swap", swap_pct)

    # ── Uptime & Load ──
    print(f"\n  {C.CYAN}Uptime & Load{C.RESET}")
    if HAS_PSUTIL:
        kv("Uptime", fmt_uptime(time.time() - psutil.boot_time()))
    else:
        kv("Uptime", fmt_uptime(_proc_uptime()))
    try:
        load = os.getloadavg()
        kv("Load (1/5/15m)", f"{load[0]:.2f}  {load[1]:.2f}  {load[2]:.2f}")
    except OSError:
        pass

    # ── Disks ──
    print(f"\n  {C.CYAN}Disks{C.RESET}")
    if HAS_PSUTIL:
        for part in psutil.disk_partitions(all=False):
            try:
                u = psutil.disk_usage(part.mountpoint)
                usage_bar(part.mountpoint, u.percent)
                kv(f"  {part.mountpoint} total", fmt_bytes(u.total), indent=4)
                kv(f"  {part.mountpoint} free",  fmt_bytes(u.free),  indent=4)
            except (PermissionError, OSError):
                pass
    else:
        try:
            out = subprocess.check_output(['df', '-h', '--output=target,size,used,avail,pcent'],
                                          text=True)
            for line in out.splitlines()[1:]:
                print(f"    {C.DIM}{line}{C.RESET}")
        except Exception:
            pass

    # ── Top Processes ──
    print(f"\n  {C.CYAN}Top Processes (by CPU){C.RESET}")
    if HAS_PSUTIL:
        # Warm up cpu_percent counters
        for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info']):
            pass
        time.sleep(0.5)
        procs = []
        for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info']):
            try:
                procs.append(p.info)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        procs.sort(key=lambda x: x['cpu_percent'] or 0, reverse=True)
        print(f"    {C.DIM}{'PID':>6}  {'CPU%':>6}  {'MEM':>8}  NAME{C.RESET}")
        for p in procs[:8]:
            mem = fmt_bytes(p['memory_info'].rss) if p['memory_info'] else '?'
            print(f"    {C.WHITE}{p['pid']:>6}  {p['cpu_percent']:>5.1f}%  {mem:>8}  {p['name']}{C.RESET}")
    else:
        try:
            out = subprocess.check_output(
                ['ps', 'aux', '--sort=-%cpu'],
                text=True
            )
            lines = out.strip().splitlines()
            print(f"    {C.DIM}{lines[0][:80]}{C.RESET}")
            for line in lines[1:9]:
                print(f"    {C.WHITE}{line[:80]}{C.RESET}")
        except Exception:
            pass


# ── Windows info (native or via powershell.exe from WSL) ────────────────────

def _run_ps(cmd):
    """Run a PowerShell command; return stdout string or '' on failure."""
    ps = 'powershell.exe' if platform.system() == 'Linux' else 'powershell'
    try:
        r = subprocess.run(
            [ps, '-NoProfile', '-NonInteractive', '-Command', cmd],
            capture_output=True, text=True, timeout=20
        )
        return r.stdout.strip()
    except Exception:
        return ''


def _ps_json(cmd):
    """Run a PowerShell command that ends in ConvertTo-Json; return parsed dict/list."""
    out = _run_ps(cmd)
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def show_windows(label):
    section(label)

    # ── OS ──
    os_data = _ps_json(
        "Get-CimInstance Win32_OperatingSystem | "
        "Select-Object Caption, Version, OSArchitecture, CSName, LastBootUpTime | "
        "ConvertTo-Json"
    )

    uptime_str = 'unknown'
    if os_data:
        kv("OS",           os_data.get('Caption', 'unknown').strip())
        kv("Version",      os_data.get('Version', 'unknown'))
        kv("Architecture", os_data.get('OSArchitecture', 'unknown'))
        kv("Hostname",     os_data.get('CSName', 'unknown'))

        # Uptime via separate command (avoids WMI date serialisation quirks)
        uptime_s = _run_ps(
            "(New-TimeSpan -Start (Get-CimInstance Win32_OperatingSystem).LastBootUpTime"
            ").TotalSeconds"
        )
        if uptime_s:
            try:
                uptime_str = fmt_uptime(float(uptime_s))
            except ValueError:
                pass

    # ── CPU ──
    print(f"\n  {C.CYAN}CPU{C.RESET}")
    cpu_data = _ps_json(
        "Get-CimInstance Win32_Processor | Select-Object -First 1 "
        "Name, NumberOfCores, NumberOfLogicalProcessors, CurrentClockSpeed | "
        "ConvertTo-Json"
    )
    if cpu_data:
        kv("Model",  cpu_data.get('Name', 'unknown').strip())
        cores   = cpu_data.get('NumberOfCores', '?')
        logical = cpu_data.get('NumberOfLogicalProcessors', '?')
        speed   = cpu_data.get('CurrentClockSpeed', '?')
        kv("Cores", f"{cores} physical  /  {logical} logical  @{speed} MHz")

    load_pct = _run_ps(
        "(Get-CimInstance Win32_Processor | "
        "Measure-Object -Property LoadPercentage -Average).Average"
    )
    if load_pct:
        try:
            usage_bar("Usage", float(load_pct))
        except ValueError:
            pass

    # ── Memory ──
    print(f"\n  {C.CYAN}Memory{C.RESET}")
    mem_data = _ps_json(
        "Get-CimInstance Win32_OperatingSystem | "
        "Select-Object TotalVisibleMemorySize, FreePhysicalMemory | "
        "ConvertTo-Json"
    )
    if mem_data:
        total = int(mem_data.get('TotalVisibleMemorySize', 0)) * 1024
        free  = int(mem_data.get('FreePhysicalMemory', 0)) * 1024
        used  = total - free
        pct   = used / total * 100 if total else 0
        kv("RAM Total", fmt_bytes(total))
        kv("RAM Used",  fmt_bytes(used))
        kv("RAM Free",  fmt_bytes(free))
        usage_bar("RAM", pct)

    # ── Uptime ──
    print(f"\n  {C.CYAN}Uptime{C.RESET}")
    kv("Uptime", uptime_str)

    # ── Disks ──
    print(f"\n  {C.CYAN}Disks{C.RESET}")
    disk_data = _ps_json(
        "Get-CimInstance Win32_LogicalDisk -Filter \"DriveType=3\" | "
        "Select-Object DeviceID, Size, FreeSpace | "
        "ConvertTo-Json"
    )
    if disk_data:
        if isinstance(disk_data, dict):
            disk_data = [disk_data]
        for disk in disk_data:
            dev  = disk.get('DeviceID', '?')
            size = int(disk.get('Size') or 0)
            free = int(disk.get('FreeSpace') or 0)
            used = size - free
            pct  = used / size * 100 if size else 0
            usage_bar(dev, pct)
            kv(f"  {dev} total", fmt_bytes(size), indent=4)
            kv(f"  {dev} free",  fmt_bytes(free),  indent=4)

    # ── Top Processes ──
    print(f"\n  {C.CYAN}Top Processes (by CPU time){C.RESET}")
    proc_data = _ps_json(
        "Get-Process | Sort-Object CPU -Descending | "
        "Select-Object -First 8 Id, ProcessName, CPU, WorkingSet | "
        "ConvertTo-Json"
    )
    if proc_data:
        if isinstance(proc_data, dict):
            proc_data = [proc_data]
        print(f"    {C.DIM}{'PID':>6}  {'CPU(s)':>8}  {'MEM':>10}  NAME{C.RESET}")
        for p in proc_data:
            pid  = p.get('Id', '?')
            name = p.get('ProcessName', '?')
            cpu  = p.get('CPU') or 0
            ws   = int(p.get('WorkingSet') or 0)
            print(f"    {C.WHITE}{pid:>6}  {cpu:>8.1f}  {fmt_bytes(ws):>10}  {name}{C.RESET}")


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    system, is_wsl, wsl_ver = detect_env()

    print(f"\n{C.BOLD}{C.BLUE}"
          "╔══════════════════════════════════════╗\n"
          "║        System Information Tool       ║\n"
          "╚══════════════════════════════════════╝"
          f"{C.RESET}")

    if not HAS_PSUTIL:
        print(f"\n  {C.YELLOW}⚠  psutil not installed — using /proc fallback for Linux info.")
        print(f"     For richer output: pip install psutil{C.RESET}")

    if system == 'Linux':
        if is_wsl:
            distro = os.environ.get('WSL_DISTRO_NAME', 'Linux')
            show_linux(f"WSL{wsl_ver} Guest  —  {distro}")
            show_windows("Windows Host")
        else:
            show_linux("Linux System")

    elif system == 'Windows':
        show_windows("Windows System")

    else:
        print(f"\n  Unsupported platform: {system}")

    print()


if __name__ == '__main__':
    main()
