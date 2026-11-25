"""An Android device or emulator, read: what adb, uiautomator, Maestro and the guest say, each a function of the text it
is handed (SMADPickleBot #2310, step 4 of #2161).

A host drives its own device (its boot, its install, its smoke, its records). What is the same for any app is here:

- the screen (`screen_texts`, `anr`, `wait_button`), the app's crash and launch (`crash_lines`, `launch_ms`);
- an install's refusal and a Maestro flow's failure in their own words (`install_error`, `maestro_failure`);
- the guest's health: how long it has been up, its load, its free space and whether it is worn (`guest_up_s`,
  `guest_load`, `data_free_kb`, `worn`), and the facts a frozen one is weighed by (`guest_facts`, `host_facts`,
  `process_mb`, `avd_config`);
- adb's own server being down (`adb_server_down`), and when a waiting smoke opens the app again (`reopen_due`).

Standard library only. Nothing here runs a command, prints or names an app.
"""
import csv
import io
import re
import xml.etree.ElementTree as ET

__all__ = ['ADB_SERVER_DOWN', 'ANR_WORDS', 'adb_server_down', 'anr', 'avd_config', 'crash_lines', 'data_free_kb', 'guest_facts',
           'guest_load', 'guest_up_s', 'host_facts', 'install_error', 'launch_ms', 'maestro_failure', 'process_mb',
           'reopen_due', 'screen_texts', 'wait_button', 'worn']

#: adb's own words when its server is gone and would not start for that call: the tool's failure, never the device's.
ADB_SERVER_DOWN = ('cannot connect to daemon', 'failed to start daemon', 'could not read ok from adb server')
#: What an Android "app isn't responding" or "keeps stopping" dialog says.
ANR_WORDS = ("isn't responding", 'keeps stopping')
_BOUNDS = re.compile(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]')


def adb_server_down(words: str) -> bool:
    """Whether a failed adb call says its own server was down, as opposed to the device refusing the command."""
    low = (words or '').lower()
    return any(w in low for w in ADB_SERVER_DOWN)


# ---- the screen ----------------------------------------------------------------------------------------------------

def screen_texts(xml: str) -> list:
    """Every text and content-desc on a uiautomator dump, in order, empty ones left out; [] for one that does not parse."""
    try:
        root = ET.fromstring(xml[xml.index('<'):] if '<' in xml else xml)
    except ET.ParseError:
        return []
    out = []
    for node in root.iter('node'):
        for attr in ('text', 'content-desc'):
            v = (node.get(attr) or '').strip()
            if v:
                out.append(v)
    return out


def anr(texts: list, words=ANR_WORDS) -> str:
    """The text of a not-responding dialog on the screen, '' when there is none; `words` in lower case."""
    return next((t for t in texts or [] if any(w in t.lower() for w in words)), '')


def wait_button(xml: str):
    """The centre of a not-responding dialog's "Wait" button in a uiautomator dump, (x, y), or None."""
    for node in re.findall(r'<node\b[^>]*>', xml or ''):
        if re.search(r'\btext="Wait"', node):
            m = _BOUNDS.search(node)
            if m:
                x1, y1, x2, y2 = (int(v) for v in m.groups())
                return (x1 + x2) // 2, (y1 + y2) // 2
    return None


# ---- the app -------------------------------------------------------------------------------------------------------

def crash_lines(log: str, package: str) -> list:
    """The crash buffer's lines that name `package`: a FATAL EXCEPTION of its process or a native crash."""
    lines = log.splitlines()
    out = []
    for i, line in enumerate(lines):
        if ('FATAL EXCEPTION' in line or 'Fatal signal' in line) and package in '\n'.join(lines[i:i + 4]):
            out.extend(lines[i:i + 4])
    return out


def launch_ms(am_output: str):
    """`am start -W`'s TotalTime in milliseconds, None when it gave none."""
    m = re.search(r'TotalTime:\s*(\d+)', am_output)
    return int(m.group(1)) if m else None


def install_error(said: str) -> str:
    """An install's own words for why it failed: the package manager's `Failure [...]`, else the first exception line,
    else the first line that says anything (a long stack's foot says nothing)."""
    m = re.search(r'Failure \[[^\]]*\]?', said) or re.search(r'^.*?[\w.]+(?:Exception|Error)\b.*$', said, re.M)
    if m:
        return m.group(0).strip()[:300]
    lines = [x.strip() for x in said.splitlines() if x.strip() and not x.strip().startswith('Performing ')]
    return (lines[0] if lines else 'adb said nothing')[:300]


def maestro_failure(report: str, said: str, code) -> str:
    """Why a Maestro flow failed: its JUnit report's <failure> text (`report` its path), else Maestro's last plain lines
    (its boxed cloud advert left out), else the exit code."""
    try:
        failure = ET.parse(report).getroot().find('.//failure')
        text = ((failure.text or failure.get('message') or '') if failure is not None else '').strip()
        if text:
            return text[:300]
    except (OSError, ET.ParseError):
        pass
    plain = [line.strip() for line in said.splitlines()
             if line.strip() and not re.match(r'[?│┌┐└┘─═║╔╗╚╝]', line.strip()) and 'maestro cloud' not in line.lower()]
    return ' | '.join(plain[-4:]) or f'maestro exited {code} and printed nothing'


def reopen_due(elapsed: float, opened: int, wait_s: float, at) -> bool:
    """Whether a smoke waiting on an update opens the app again now: `elapsed` s after its first open, having reopened
    `opened` times. Each time in `at` is one reopen, in order; one at or past `wait_s` never happens."""
    return opened < len(at) and at[opened] < wait_s and elapsed > at[opened]


# ---- the guest -----------------------------------------------------------------------------------------------------

def guest_up_s(uptime: str):
    """The guest's seconds up from /proc/uptime's first figure, None when the text has none."""
    try:
        return round(float((uptime or '').split()[0]))
    except (IndexError, ValueError):
        return None


def guest_load(text: str):
    """The guest's 1-minute load from /proc/loadavg's line, after /proc/uptime's, None when there is none."""
    m = re.search(r'^(\d+\.\d+) \d+\.\d+ \d+\.\d+ \d+/\d+ ', text or '', re.M)
    return float(m.group(1)) if m else None


def data_free_kb(df_text: str):
    """/data's free kilobytes from `adb shell df /data` (Available is the fourth column of its one data line), None when
    the text has no such line."""
    for line in df_text.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0].startswith('/dev/'):
            try:
                return int(parts[3])
            except ValueError:
                return None
    return None


def worn(meminfo: str, uptime: str, max_up_s: float) -> str:
    """Why an emulator is too worn for a smoke, '' when it is fresh enough or its uptime cannot be parsed: up past
    `max_up_s` (/proc/uptime's first figure), its swap in use (/proc/meminfo) said beside it. Swap alone never makes it
    worn: a guest swaps a gigabyte within minutes of a boot."""
    try:
        up = float((uptime or '').split()[0])
    except (IndexError, ValueError):
        return ''
    if up <= max_up_s:
        return ''
    why = [f'up {up / 3600:.0f} h']
    kb = {k: int(v) for k, v in re.findall(r'^(SwapTotal|SwapFree):\s+(\d+)\s*kB', meminfo or '', re.M)}
    if kb.get('SwapTotal') and kb.get('SwapFree') is not None:
        why.append(f"{(kb['SwapTotal'] - kb['SwapFree']) // 1024} of {kb['SwapTotal'] // 1024} MB swap in use")
    return ', '.join(why)


def guest_facts(text: str) -> dict:
    """The guest's own memory, swap, uptime and load from one `cat /proc/meminfo /proc/uptime /proc/loadavg`; a figure
    that cannot be read is left out."""
    kb = {k: int(v) for k, v in re.findall(r'^(MemTotal|MemAvailable|SwapTotal|SwapFree):\s+(\d+)\s*kB', text or '', re.M)}
    out = {}
    if 'MemAvailable' in kb:
        out['guest_free_mb'] = kb['MemAvailable'] // 1024
    if kb.get('SwapTotal') and 'SwapFree' in kb:
        out['guest_swap_mb'] = f"{(kb['SwapTotal'] - kb['SwapFree']) // 1024}/{kb['SwapTotal'] // 1024}"
    up = re.search(r'^(\d+(?:\.\d+)?) \d+(?:\.\d+)?\s*$', text or '', re.M)
    if up:
        out['guest_up_h'] = round(float(up.group(1)) / 3600, 1)
    load = re.search(r'^(\d+\.\d+) \d+\.\d+ \d+\.\d+ \d+/\d+', text or '', re.M)
    if load:
        out['guest_load'] = float(load.group(1))
    return out


def host_facts(text: str) -> dict:
    """The box's CPU load and free memory from one line `<cpu %> <free MB> <total MB>`; {} when it is not that."""
    m = re.search(r'(\d+(?:\.\d+)?)\s+(\d+)\s+(\d+)', text or '')
    return {'box_cpu': round(float(m.group(1))), 'box_free_mb': int(m.group(2)), 'box_total_mb': int(m.group(3))} if m else {}


def process_mb(tasklist_csv: str, watched) -> dict:
    """Megabytes held by the `watched` processes ((label, name prefix) pairs) from Windows' `tasklist /FO CSV /NH`, each
    summed; {} when nothing parses."""
    out = {}
    for row in csv.reader(io.StringIO(tasklist_csv or '')):
        if len(row) < 5:
            continue
        digits = re.sub(r'\D', '', row[4])
        for label, prefix in watched:
            if digits and row[0].lower().startswith(prefix):
                out[label] = out.get(label, 0) + int(digits) // 1024
    return out


def avd_config(text: str) -> dict:
    """An AVD's RAM, Java heap and cores from its config.ini, as written; a key written twice is its last line, the one
    the emulator reads."""
    out = {}
    for key, name in (('hw.ramSize', 'ram'), ('vm.heapSize', 'heap'), ('hw.cpu.ncore', 'cores')):
        found = re.findall(rf'^{re.escape(key)}\s*=\s*(\S+)', text or '', re.M)
        if found:
            out[name] = found[-1]
    return out
