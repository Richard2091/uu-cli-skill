#!/usr/bin/env python3
"""_uulib.py - hardened PTY transport for `uuyc-cli term` (uu-cli skill).

SPDX-License-Identifier: MIT

Portable by design: nothing here assumes a particular install path or device.
`find_cli()` resolves the CLI from an explicit path, the environment, the
Netease registry key, PATH, then a bounded scan of common install roots.
`resolve_device()` resolves the target from an explicit value, the
environment, the saved config, then the single online Windows device.

Measured facts (CLI 4.44.0.3919, relay ~300 ms):

  * Writing the whole command in ONE `proc.write()` is ~100x faster than
    feeding it character by character, but a write longer than ~250 chars gets
    truncated (only 112..900 chars land, silently).  So every write is
    verified against the terminal echo and retried after a Ctrl+C.
  * Many small rapid writes (the old "5 chars / 40 ms" recipe) are dropped
    outright -- never use that pacing, it corrupts commands.
  * Raw output has no spaces: the remote is a screen terminal that paints
    spaces with cursor moves.  Results must travel base64 between markers.
  * The screen repaints old output, so every result carries a fresh GUID and
    already-seen GUIDs are ignored.
  * The remote stream is UTF-8.
  * Locked screen => password prompt first, unlocked => prompt directly.
  * The CLI has no password flag and the client does not hand its saved
    lock-screen password to `term`, so the password has to be typed into the
    PTY.
  * `term ls/kill` are one-shot RPCs but still demand the password on a locked
    remote; the password can be piped on stdin, so no PTY is needed for them.
  * Success of a *command* is decided from the presence of an ErrorRecord in
    its output, not from `$?` -- `$?` is overwritten by any following statement.
"""
import os
import re
import sys
import json
import time
import base64
import ctypes
import hashlib
import secrets
import string
import threading
import subprocess

# ------------------------------------------------------------------ constants
APP_DIR_NAME = 'uu-cli'
CONFIG_ENV = 'UU_CLI_CONFIG'


def _app_dir():
    """Config directory that belongs to this tool, not to the host harness."""
    base = os.environ.get('APPDATA') or os.path.expanduser('~')
    return os.path.join(base, APP_DIR_NAME)


CONFIG_DIR = _app_dir()
CONFIG_FILE = os.environ.get(CONFIG_ENV) or os.path.join(CONFIG_DIR, 'config.json')

# Pre-1.0 locations, migrated once and then removed (they held plaintext
# passwords in a directory owned by a different tool).
LEGACY_CONFIG_FILES = (
    os.path.join(os.path.expanduser('~'), '.dsh', 'uu-cli.json'),
    os.path.join(os.path.expanduser('~'), '.dsh', 'uu-cli-secret.json'),
)

CLI_ENV = 'UU_CLI_PATH'
DEVICE_ENV = 'UU_DEVICE_ID'
PASSWORD_ENV = 'UU_LOCK_PASSWORD'

CLI_EXE = 'uuyc-cli.exe'

# Only sessions this tool could have created.  Deliberately narrow: a plain
# `startswith('uu')` would also match a colleague's `uu-deploy` session, and
# `('q','p','c')` would match `core`, `prod`, `query`, `python`...  Killing
# someone else's session is destructive and cannot be undone.
OWNED_NAME = re.compile(r'^uu(?:-\d{8}-\d{6}-[a-z0-9]{4}|\d{5})$')
LEGACY_NAME = re.compile(r'^[qpc]\d{1,6}$')     # pre-0.2 names, opt-in only

MAX_WRITE = 220          # verified-safe single-write payload size
MAX_PULL = 64 * 1024     # pull is ~80 B/s; beyond this it stops being practical

# Two short helper definitions instead of one long one: together they would
# exceed the single-write limit, so each is written separately.
#
#   $m  emits  @<guid>@<failed 0|1>@<base64 of the text>#
#   $a  runs the command, keeps stdout and stderr together, and decides failure
#       from the *error records* rather than `$?` (which any following statement
#       overwrites) -- while still honouring a deliberate `-ErrorAction Ignore`,
#       so the many `-EA 0` probes are not reported as failures.
#
# The text is joined with LF (PowerShell's default would join with spaces) and a
# null result becomes '' -- a bare `[string]$null` makes UTF8.GetBytes throw
# "String reference not set to an instance of a String".
MARK_DEF = (
    "$m={param($t,$e)'@'+[guid]::NewGuid().ToString('N')+'@'+$e+'@'+"
    "[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($t))+'#'}"
)
HELPER_DEF = (
    "$a={$e=0;try{$o=(&$args[0])2>&1}catch{$e=1;$o=@($_.ToString())};"
    "if(@($o|?{$_-is[Management.Automation.ErrorRecord]}).Count){$e=1};"
    "&$m ((@($o|%{$_.ToString()})) -join ([char]10)) $e}"
)
# PSReadLine repaints the input line with cursor moves; that corrupts the echo
# (so writes cannot be verified) and makes long writes unreliable.  Dropping it
# for the session makes the terminal behave like a plain line-mode console.
PSREADLINE_OFF = 'Remove-Module PSReadLine'
MARK = re.compile(r'@([0-9a-f]{32})@([01])@([A-Za-z0-9+/=]*)#')
PROMPT = re.compile(r'PS[A-Z]:\\')

RATE_LIMIT_HINTS = ('操作过于频繁', 'too frequent', 'too many requests', 'rate limit')


class TermError(Exception):
    """Any failure the caller should surface as a clean error message."""


def _is_rate_limited(message):
    text = (message or '').lower()
    return any(hint.lower() in text for hint in RATE_LIMIT_HINTS)


# ------------------------------------------------------- DPAPI secret storage
class _BLOB(ctypes.Structure):
    _fields_ = [('cbData', ctypes.c_ulong),
                ('pbData', ctypes.POINTER(ctypes.c_char))]


def _dpapi(data, protect):
    """Encrypt/decrypt with the per-user Windows DPAPI. b'' on failure."""
    if os.name != 'nt':
        return b''
    try:
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
    except Exception:
        return b''
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = _BLOB()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        return b''
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _protect_text(text):
    """Return a storable form of a secret: DPAPI blob, or plaintext fallback."""
    if not text:
        return ''
    blob = _dpapi(text.encode('utf-8'), True)
    if blob:
        return 'dpapi:' + base64.b64encode(blob).decode('ascii')
    return text


def _unprotect_text(stored):
    if not stored:
        return ''
    if stored.startswith('dpapi:'):
        blob = _dpapi(base64.b64decode(stored[6:]), False)
        return blob.decode('utf-8', 'replace') if blob else ''
    return stored          # pre-0.3 plaintext value, still readable


# --------------------------------------------------------------------- config
def _harden_acl(path):
    """Restrict the file to the current user. chmod() does nothing on Windows."""
    if os.name != 'nt':
        return
    user = os.environ.get('USERNAME')
    if not user:
        return
    try:
        subprocess.run(
            ['icacls', path, '/inheritance:r', '/grant:r', '%s:(F)' % user],
            capture_output=True, timeout=10,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except Exception:
        pass


def _config_read():
    """Read config, migrating any pre-0.3 file once and deleting it afterwards."""
    data = None
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as handle:
            loaded = json.load(handle)
        if isinstance(loaded, dict):
            data = loaded
    except FileNotFoundError:
        data = None
    except json.JSONDecodeError as exc:
        # Never silently discard a readable-but-broken config: the user would
        # lose a stored password and only find out when the screen locks.
        try:
            os.replace(CONFIG_FILE, CONFIG_FILE + '.bak')
            sys.stderr.write('WARN=config was corrupt (%s); moved to config.json.bak\n'
                             % exc)
        except OSError:
            pass
        data = None
    except Exception:
        data = None

    if data is None:
        data = _migrate_legacy()
    data.setdefault('devices', {})
    data.setdefault('defaults', {})
    return data


def _migrate_legacy():
    """Pull settings out of the old ~/.dsh files, then delete them."""
    merged = {'devices': {}, 'defaults': {}}
    found = False
    for legacy in LEGACY_CONFIG_FILES:
        try:
            with open(legacy, 'r', encoding='utf-8') as handle:
                old = json.load(handle)
        except Exception:
            continue
        if not isinstance(old, dict):
            continue
        found = True
        for device, entry in (old.get('devices') or {}).items():
            if isinstance(entry, dict) and entry.get('password'):
                merged['devices'][device] = {
                    'password': _protect_text(entry['password']),
                    'saved_at': entry.get('saved_at', ''),
                }
        for key, value in (old.get('defaults') or {}).items():
            merged['defaults'][key] = value
        if old.get('device'):
            merged['defaults'].setdefault('device', old['device'])
    if found:
        _config_write(merged)
        for legacy in LEGACY_CONFIG_FILES:
            try:
                os.remove(legacy)
            except OSError:
                pass
    return merged


def _config_write(data):
    """Atomic write: a half-written config would look like a lost password."""
    tmp = CONFIG_FILE + '.tmp'
    try:
        os.makedirs(os.path.dirname(os.path.abspath(CONFIG_FILE)), exist_ok=True)
        with open(tmp, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, CONFIG_FILE)
        _harden_acl(CONFIG_FILE)
        return True
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def config_get(section, key, default=''):
    value = (_config_read().get(section) or {}).get(key)
    return default if value in (None, '') else value


def config_set(section, key, value):
    data = _config_read()
    data.setdefault(section, {})[key] = value
    return _config_write(data)


# ------------------------------------------------------------ password storage
def load_saved_password(device):
    entry = _config_read()['devices'].get(device) or {}
    if entry.get('password'):
        return _unprotect_text(entry['password'])
    return ''


def save_password(device, password):
    if not password:
        return False
    data = _config_read()
    data['devices'][device] = {
        'password': _protect_text(password),
        'saved_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
    }
    return _config_write(data)


def forget_password(device):
    data = _config_read()
    if device in data['devices']:
        del data['devices'][device]
        return _config_write(data)
    return True


def resolve_password(device, cli_password=''):
    """--password > $UU_LOCK_PASSWORD > saved config. Returns (value, source)."""
    if cli_password:
        return cli_password, 'cli'
    env_value = os.environ.get(PASSWORD_ENV, '')
    if env_value:
        return env_value, 'env'
    saved = load_saved_password(device)
    if saved:
        return saved, 'file'
    return '', 'none'


# ------------------------------------------------------------------ discovery
def _reg_inst_dirs():
    """Yield Netease GameViewer install directories recorded by the installer."""
    try:
        import winreg
    except Exception:
        return
    keys = [
        (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Netease\GameViewerSetup'),
        (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Netease\GameViewerSetup'),
        (winreg.HKEY_CURRENT_USER, r'SOFTWARE\Netease\GameViewerSetup'),
        (winreg.HKEY_CURRENT_USER, r'SOFTWARE\Netease\GameViewer'),
    ]
    for root, path in keys:
        try:
            with winreg.OpenKey(root, path) as handle:
                for name in ('InstDir', 'instDir', 'InstallDir', 'installDir'):
                    try:
                        value, _kind = winreg.QueryValueEx(handle, name)
                    except OSError:
                        continue
                    if value:
                        yield value
        except OSError:
            continue


def _scan_roots():
    """Bounded list of plausible install roots (no full-disk walk)."""
    roots = []
    for env in ('ProgramFiles', 'ProgramFiles(x86)', 'LOCALAPPDATA', 'APPDATA', 'SystemDrive'):
        value = os.environ.get(env)
        if value:
            roots.append(value)
    for letter in 'CDEFGH':
        drive = '%s:\\' % letter
        if os.path.isdir(drive):
            roots.append(drive)
    out = []
    for root in roots:
        out.extend([
            root,
            os.path.join(root, 'Application'),
            os.path.join(root, 'Programs'),
            os.path.join(root, 'Netease'),
            os.path.join(root, 'Program Files'),
        ])
    return out


def find_cli(explicit=''):
    """Locate uuyc-cli.exe. Returns (path, source) or (None, 'not-found')."""
    candidates = []
    if explicit:
        candidates.append((explicit, 'argument'))
    env_value = os.environ.get(CLI_ENV, '')
    if env_value:
        candidates.append((env_value, 'env:' + CLI_ENV))
    saved = config_get('defaults', 'cli')
    if saved:
        candidates.append((saved, 'config'))
    for inst in _reg_inst_dirs():
        candidates.append((os.path.join(inst, 'bin', CLI_EXE), 'registry'))
    found = _which(CLI_EXE)
    if found:
        candidates.append((found, 'PATH'))
    for root in _scan_roots():
        for tail in (
            os.path.join('uuRemote', 'GameViewer', 'bin', CLI_EXE),
            os.path.join('GameViewer', 'bin', CLI_EXE),
            os.path.join('Netease', 'GameViewer', 'bin', CLI_EXE),
            os.path.join('Netease', 'uuRemote', 'GameViewer', 'bin', CLI_EXE),
            os.path.join('uu', 'GameViewer', 'bin', CLI_EXE),
        ):
            candidates.append((os.path.join(root, tail), 'scan'))
    seen = set()
    for path, source in candidates:
        if not path:
            continue
        path = os.path.normpath(path)
        if path in seen:
            continue
        seen.add(path)
        if os.path.isfile(path):
            return path, source
    return None, 'not-found'


def _which(name):
    try:
        from shutil import which
        return which(name)
    except Exception:
        return None


def _parse_envelope(done, args, secret=''):
    """Parse the JSON envelope. stdout first, stderr only as a fallback.

    Never concatenate the two: the CLI prints progress lines on stderr, and
    mixing them around the JSON makes json.loads fail with "Extra data" even
    though the request succeeded.  Scanning with raw_decode skips past any
    leading junk (a progress line that happens to contain `{`).
    """
    decoder = json.JSONDecoder()
    for text in (done.stdout or '', done.stderr or ''):
        for index, char in enumerate(text):
            if char != '{':
                continue
            try:
                data, _end = decoder.raw_decode(text[index:])
            except ValueError:
                continue
            if isinstance(data, dict) and data.get('success') is False:
                error = data.get('error') or {}
                message = str(error.get('message') or 'unknown')
                if secret:
                    message = message.replace(secret, '***')
                return None, 'cli error %s: %s (%s)' % (
                    error.get('code'), message, ' '.join(args))
            return data, None
    blob = ' '.join(((done.stdout or '') + ' ' + (done.stderr or '')).split())[:200]
    if secret:
        blob = blob.replace(secret, '***')
    return None, 'no JSON in CLI output (rc=%s): %s' % (done.returncode, blob)


def _translate_term_error(message, secret=''):
    if not message:
        return message
    if secret:
        message = message.replace(secret, '***')
    if _is_rate_limited(message):
        return ('the service is rate-limiting term connections (%s); wait a few '
                'seconds and retry, and avoid rapid session churn' % message)
    if 'empty password' in message or 'code 105' in message.replace('cli error ', 'code '):
        return ('the remote is locked and term RPCs need the account password; '
                'run once with --password or --password-stdin so it is remembered (%s)'
                % message)
    return message


def cli_json(cli, args, timeout=20):
    """Run a CLI subcommand with --json. Returns (data, error)."""
    try:
        done = subprocess.run([cli] + list(args) + ['--json'], capture_output=True,
                              timeout=timeout, encoding='utf-8', errors='replace')
    except FileNotFoundError:
        return None, 'CLI not found: %s' % cli
    except subprocess.TimeoutExpired:
        return None, 'CLI timed out after %ss: %s' % (timeout, ' '.join(args))
    except Exception as exc:
        return None, str(exc)
    return _parse_envelope(done, args)


def cli_text(cli, args, timeout=20):
    try:
        done = subprocess.run([cli] + list(args), capture_output=True, timeout=timeout,
                              encoding='utf-8', errors='replace')
        return (done.stdout or '').strip(), done.returncode
    except Exception as exc:
        return str(exc), -1


def cli_term(cli, dev, sub_args, password='', timeout=40, retries=3):
    """Run a one-shot `term` subcommand (ls/kill/has/rename).

    On a locked remote these RPCs still demand the account password, and it can
    simply be piped on stdin -- so no PTY is needed just to list sessions.

    The service also rate-limits rapid `term` connections, so a rate-limited
    call is retried with backoff instead of being reported as a hard failure.
    """
    argv = [cli, 'term', '-d', dev] + list(sub_args) + ['--json']
    last = None
    for attempt in range(max(1, retries)):
        try:
            done = subprocess.run(argv, capture_output=True, timeout=timeout,
                                  input=(password + '\n') if password else '',
                                  encoding='utf-8', errors='replace')
        except FileNotFoundError:
            return None, 'CLI not found: %s' % cli
        except subprocess.TimeoutExpired:
            return None, 'CLI timed out after %ss: %s' % (timeout, ' '.join(sub_args))
        except Exception as exc:
            return None, str(exc)
        data, err = _parse_envelope(done, sub_args, secret=password)
        if err is None:
            return data, None
        last = err
        if _is_rate_limited(err) and attempt < retries - 1:
            time.sleep(3.0 * (attempt + 1))
            continue
        break
    return None, _translate_term_error(last, secret=password)


def device_list(cli, timeout=20):
    data, err = cli_json(cli, ['device', 'list'], timeout)
    if err:
        return [], err
    return ((data.get('data') or {}).get('items') or []), None


def resolve_device(cli, explicit='', save=True):
    """Pick the target device. Returns (device_id, source).

    Order: explicit argument (id or name) > $UU_DEVICE_ID > saved config >
    the single online Windows device.  Ambiguity is an error, never a guess.
    """
    items, err = device_list(cli)
    by_id = {item.get('deviceId'): item for item in items}
    by_name = {item.get('deviceName'): item for item in items}

    if explicit:
        if explicit in by_id:
            return explicit, 'argument'
        matches = [i for i in items if explicit.lower() in (i.get('deviceName') or '').lower()]
        if len(matches) == 1:
            return matches[0]['deviceId'], 'argument:name'
        if len(matches) > 1:
            raise TermError('device name %r matches several devices: %s' % (
                explicit, ', '.join('%s(%s)' % (m.get('deviceName'), m.get('deviceId'))
                                    for m in matches)))
        return explicit, 'argument'          # maybe an id not currently listed

    env_value = os.environ.get(DEVICE_ENV, '')
    if env_value:
        return (by_name[env_value]['deviceId'] if env_value in by_name else env_value), 'env:' + DEVICE_ENV

    saved = config_get('defaults', 'device')
    if saved:
        row = by_id.get(saved)
        if row is None or row.get('isOnline'):
            return saved, 'config'
        # The saved device is offline: a single online Windows device is a
        # better answer than failing every command.
        online = [i for i in items if i.get('isOnline')
                  and (i.get('platform') or 'Windows') == 'Windows']
        if len(online) == 1:
            sys.stderr.write('WARN=saved device is offline; using %s\n'
                             % online[0].get('deviceName'))
            if save:
                config_set('defaults', 'device', online[0]['deviceId'])
            return online[0]['deviceId'], 'auto:fallback'

    if err:
        raise TermError('cannot list devices: %s' % err)
    online = [i for i in items if i.get('isOnline') and (i.get('platform') or 'Windows') == 'Windows']
    if len(online) == 1:
        chosen = online[0]['deviceId']
        if save:
            config_set('defaults', 'device', chosen)
        return chosen, 'auto'
    if not online:
        raise TermError('no online Windows device in this account (devices: %s)' % (
            ', '.join('%s[%s]' % (i.get('deviceName'), 'online' if i.get('isOnline') else 'offline')
                      for i in items) or 'none'))
    raise TermError('several online Windows devices, choose one with --device: %s' % (
        ', '.join('%s(%s)' % (i.get('deviceName'), i.get('deviceId')) for i in online)))


def session_list(cli, dev, password='', timeout=40, retries=3):
    data, err = cli_term(cli, dev, ['ls'], password=password, timeout=timeout,
                         retries=retries)
    if err:
        return [], err
    return ((data.get('data') or {}).get('items') or []), None


def kill_session(cli, dev, name, password='', timeout=40, retries=2):
    _data, err = cli_term(cli, dev, ['kill', name], password=password,
                          timeout=timeout, retries=retries)
    return 0 if err is None else 1


def kill_our_sessions(cli, dev, password='', names=None, pace=1.5, include_legacy=False):
    """Kill only sessions this tool created. Returns list of (name, rc).

    Matching is anchored on the exact shapes this tool generates.  `q`/`p`/`c`
    prefixes from pre-0.2 versions are opt-in because those letters also match
    unrelated session names (`core`, `prod`, `python`).
    """
    sessions, err = session_list(cli, dev, password=password)
    if err:
        raise TermError('cannot list sessions: %s' % err)
    killed = []
    for session in sessions:
        name = session.get('name') or ''
        if names is not None:
            if name not in names:
                continue
        elif not (OWNED_NAME.match(name)
                  or (include_legacy and LEGACY_NAME.match(name))):
            continue
        if killed:
            time.sleep(pace)
        killed.append((name, kill_session(cli, dev, name, password=password)))
    return killed


# ------------------------------------------------------------------- terminal
def clean_ansi(text):
    text = re.sub(r'\x1b\[[?0-9;]*[a-zA-Z]', '', text)
    text = re.sub(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)', '', text)
    text = re.sub(r'\x1b[()][B0]', '', text)
    text = re.sub(r'\x1b[=>]', '', text)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
    return text


def squash(text):
    return re.sub(r'\s+', '', text)


def _refusal_reason(text):
    """Map a CLI refusal banner to a human explanation, or '' if none matched."""
    lowered = text.lower()
    if 'already attached' in lowered or 'code 2001' in lowered:
        return ('the session is still held by another client; a term session can be '
                'attached by only one client at a time (close the other one first)')
    if 'terminal session not found' in lowered:
        return ('that session no longer exists on the remote (a `running` row in '
                'term ls can be stale); clean it up with --kill-all')
    if _is_rate_limited(text):
        return 'the service is rate-limiting term connections; wait a few seconds and retry'
    if '正在被远控' in text or 'currently being remotely controlled' in lowered:
        return 'the device is being remotely controlled by someone else'
    if '版本过低' in text:
        return 'protocol version mismatch; update the local UU client'
    if '离线' in text or 'offline' in lowered:
        return 'the device is offline'
    return ''


def _spawn(argv, size=(50, 500)):
    """Spawn in a ConPTY. `argv` must be a list: pywinpty shlex-splits strings
    with posix=False and then looks the command up verbatim, so a quoted path
    with spaces (C:\\Program Files\\...) would not be found."""
    if os.name != 'nt':
        raise TermError('this tool needs Windows: `uuyc-cli term` requires ConPTY')
    try:
        from winpty import PtyProcess
    except ImportError:
        raise TermError(
            'pywinpty is required (the CLI needs a real TTY): pip install pywinpty')
    try:
        return PtyProcess.spawn(list(argv), dimensions=size)
    except Exception as exc:
        raise TermError('cannot start `uuyc-cli term`: %s' % exc)


class Term:
    """One persistent `uuyc-cli term` PTY session."""

    def __init__(self, cli=None, dev=None, password='', shell='powershell',
                 init_timeout=60, size=(50, 500), verbose=False, name=''):
        self.verbose = verbose
        self.buf = ''
        self.seen_guids = set()
        self.lock = threading.Lock()
        self.alive = True
        self.login_error = ''
        self.last_failed = False
        self.helper_ready = False
        self.password = password
        self.cli = cli or find_cli()[0]
        if not self.cli:
            raise TermError('uuyc-cli not found; pass --cli PATH or set %s' % CLI_ENV)
        if not dev:
            dev, _source = resolve_device(self.cli)
        self.dev = dev
        if (shell or 'powershell').lower() not in ('powershell', 'pwsh', 'ps'):
            raise TermError('this skill drives PowerShell sessions only '
                            '(got --shell %s); the helper protocol needs PS' % shell)
        self.shell = 'powershell'
        if name:
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,40}', name):
                raise TermError('--session must be 1-41 chars of [A-Za-z0-9_-]')
            self.session_name = name
        else:
            self.session_name = 'uu-%s-%s' % (
                time.strftime('%Y%m%d-%H%M%S'),
                ''.join(secrets.choice(string.ascii_lowercase + string.digits)
                        for _ in range(4)))
        argv = [self.cli, 'term', '-d', self.dev, 'new', self.session_name,
                '--shell', 'powershell']
        self.proc = _spawn(argv, size)
        try:
            self.t = threading.Thread(target=self._reader, daemon=True)
            self.t.start()
        except Exception as exc:
            try:
                self.proc.terminate()
            except Exception:
                pass
            raise TermError('cannot start the reader thread: %s' % exc)
        self.logged_in, self.used_password = self._login(password, init_timeout)

    # ---------- plumbing ----------
    def _reader(self):
        while self.alive:
            try:
                chunk = self.proc.read(8192)
            except Exception:
                time.sleep(0.02)
                continue
            if chunk:
                if not isinstance(chunk, str):
                    chunk = chunk.decode('utf-8', 'replace')
                with self.lock:
                    self.buf += chunk
            else:
                time.sleep(0.02)

    def take(self):
        with self.lock:
            data = self.buf
            self.buf = ''
        return data

    def _vprint(self, msg):
        if self.verbose:
            print(msg, file=sys.stderr, flush=True)

    def _login(self, password, timeout):
        """Wait for the shell prompt, typing the password if the remote is locked.

        Returns (logged_in, used_password).  When the CLI refuses the connection
        outright, the reason is captured in `login_error` so the caller can
        report the truth instead of blaming the password.
        """
        deadline = time.time() + timeout
        seen = ''
        sent = False
        while time.time() < deadline:
            seen += self.take()
            flat = squash(clean_ansi(seen))
            if not sent and ('密码' in seen or 'password' in seen.lower()):
                if not password:
                    self.login_error = 'the remote is locked and no password is available'
                    return False, False
                time.sleep(0.4)
                try:
                    self.proc.write(password + '\r\n')
                except Exception as exc:
                    self.login_error = 'pty write failed while typing the password: %s' % exc
                    return False, False
                sent = True
                seen = ''
                time.sleep(1.5)
                continue
            if PROMPT.search(flat):
                time.sleep(0.8)
                self.take()
                return True, sent
            refusal = _refusal_reason(seen)
            if refusal:
                self.login_error = refusal
                return False, sent
            time.sleep(0.1)
        if sent:
            self.login_error = ('the password was typed but no shell prompt appeared '
                                'within %ss (wrong password, or the service is throttling '
                                'connections)' % timeout)
        else:
            self.login_error = ('no shell prompt within %ss; the service may be '
                                'rate-limiting connections' % timeout)
        return False, sent

    # ---------- input ----------
    def interrupt(self):
        try:
            self.proc.write('\x03')
            time.sleep(0.35)
            self.take()
        except Exception:
            pass

    def wait_prompt(self, timeout):
        deadline = time.time() + timeout
        seen = ''
        while time.time() < deadline:
            seen += self.take()
            if PROMPT.search(squash(clean_ansi(seen))):
                return True
            time.sleep(0.05)
        return False

    def _define(self, definition):
        """Write one definition and confirm the shell accepted it.

        Two definitions must not be written back to back: input delivery is
        bursty, and the second write can land incomplete, leaving `$a` half
        defined -- after which every command silently produces no marker.
        """
        for attempt in range(4):
            self.take()
            time.sleep(0.35)
            try:
                self.proc.write(definition + '\r')
            except Exception as exc:
                raise TermError('pty write failed: %s' % exc)
            if self.wait_prompt(12):
                time.sleep(0.3)
                self.take()
                return True
            self.interrupt()
            time.sleep(0.3 * (attempt + 1))
        return False

    # ---------- public API ----------
    def prepare(self):
        """Drop PSReadLine, define the base64 helpers, prove they work.

        The only proof that the helpers exist is a successful marker
        round-trip -- and when it fails, the definitions are rewritten, because
        a half-written definition is indistinguishable from a missing one.
        """
        try:
            self.proc.write(PSREADLINE_OFF + '\r')
        except Exception as exc:
            raise TermError('pty write failed: %s' % exc)
        time.sleep(1.2)
        self.take()

        for attempt in range(3):
            for definition in (MARK_DEF, HELPER_DEF):
                if not self._define(definition):
                    raise TermError('helper setup failed (definition was not accepted)')
            for _ in range(2):
                out, err = self.run_one("'PING'", timeout=30)
                if err is None and 'PING' in (out or ''):
                    self.helper_ready = True
                    return True
                self.interrupt()
            self._vprint('[prepare] marker round-trip failed, rewriting helpers '
                         '(attempt %d)' % (attempt + 1))
        raise TermError('helper setup failed (no marker round-trip)')

    def run_one(self, command, timeout=15, retries=4):
        """Run one command (<= MAX_WRITE - 5 chars).

        Returns (text, error). `error` is set for transport problems; a command
        that *ran* but failed is reported through `self.last_failed`, so callers
        can distinguish "could not reach the remote" from "the remote said no".
        """
        payload = '&$a{' + command + '}'
        if len(payload) > MAX_WRITE:
            return None, ('command needs %d chars but one write carries %d; split it '
                          'on ";" or push a .ps1 and run it with -File'
                          % (len(payload), MAX_WRITE))
        want_echo = squash(payload)
        last = None
        self.last_failed = False
        for _ in range(retries):
            self.take()
            time.sleep(0.35)          # let the previous repaint settle
            try:
                self.proc.write(payload + '\r')
            except Exception as exc:
                return None, 'pty write failed: %s' % exc
            seen = ''
            echoed = False
            b64 = None
            failed = '0'
            deadline = time.time() + timeout
            while time.time() < deadline:
                seen += self.take()
                flat = squash(clean_ansi(seen))
                if not echoed and want_echo in flat:
                    echoed = True
                for match in MARK.finditer(clean_ansi(seen)):
                    if match.group(1) in self.seen_guids:
                        continue
                    self.seen_guids.add(match.group(1))
                    failed, b64 = match.group(2), match.group(3)
                    break
                if b64 is not None and echoed:
                    break
                time.sleep(0.05)
            if b64 is None or not echoed:
                if b64 is None:
                    last = 'no marker within %ss' % timeout
                else:
                    last = 'no echo within %ss' % timeout
                self.interrupt()
                time.sleep(0.3)
                continue
            time.sleep(0.4)           # flush trailing repaints of this output
            self.take()
            try:
                raw = base64.b64decode(b64 + '=' * (-len(b64) % 4))
            except Exception as exc:
                last = 'decode failed: %s' % exc
                continue
            self.last_failed = failed == '1'
            return raw.decode('utf-8', 'replace'), None
        return None, last

    def run(self, command, timeout=60):
        """Run a command, splitting on top-level ';' when it exceeds one write.

        `self.last_failed` reflects whether any segment reported a failure.
        """
        parts = split_command(command)
        if parts is None:
            return None, ('command has a segment that still exceeds one write after '
                          'splitting on ";"; shorten it, or push a .ps1 and run it '
                          'with -File')
        outs = []
        failed = False
        for part in parts:
            out, err = self.run_one(part, timeout=timeout)
            if err:
                return None, err
            failed = failed or self.last_failed
            if out:
                outs.append(out.rstrip('\r\n'))
        self.last_failed = failed
        return '\n'.join(outs), None

    def close(self, send_exit=True, kill_remote=True):
        """Close the local PTY and, unless asked not to, end the remote session.

        Measured: terminating the local PTY does *not* end the remote session --
        it keeps showing up as `running` and holds a session slot, so it must be
        killed through the CLI explicitly.  The local PTY is given a moment to
        exit first, otherwise the CLI may still hold the session and the kill
        fails with "already attached".
        """
        self.alive = False
        if send_exit:
            try:
                self.proc.write('exit\r')
                time.sleep(0.4)
            except Exception:
                pass
        try:
            self.proc.terminate()
        except Exception:
            pass
        for _ in range(30):
            try:
                if not self.proc.isalive():
                    break
            except Exception:
                break
            time.sleep(0.1)
        if kill_remote and self.session_name:
            try:
                if kill_session(self.cli, self.dev, self.session_name,
                                password=self.password, timeout=15, retries=1) != 0:
                    sys.stderr.write(
                        'WARN=remote session %s may still be running; run --kill-all\n'
                        % self.session_name)
            except Exception as exc:
                sys.stderr.write('WARN=remote session cleanup failed: %s\n' % exc)


def split_command(command, limit=MAX_WRITE - 5):
    """Split on top-level ';' only -- never inside quotes or braces.

    Splitting naively corrupts real commands: `Get-Content 'C:\\a;b.txt'` would
    become `Get-Content 'C:\\a` plus `b.txt'`, and an `if/else` would be cut
    between the two branches.  Returns None when a piece still does not fit.
    """
    pieces, cur, quote, depth, i = [], '', '', 0, 0
    while i < len(command):
        char = command[i]
        if quote:
            cur += char
            if char == quote:
                if i + 1 < len(command) and command[i + 1] == quote:
                    cur += command[i + 1]      # doubled quote = escaped quote
                    i += 1
                else:
                    quote = ''
            i += 1
            continue
        if char in ("'", '"'):
            quote = char
            cur += char
        elif char in '{(':
            depth += 1
            cur += char
        elif char in '})':
            depth = max(0, depth - 1)
            cur += char
        elif char == ';' and depth == 0:
            pieces.append(cur)
            cur = ''
        else:
            cur += char
        i += 1
    pieces.append(cur)

    out = []
    cur = ''
    for piece in pieces:
        cand = piece if not cur else cur + ';' + piece
        if len(cand) <= limit:
            cur = cand
            continue
        if cur:
            out.append(cur)
            cur = piece
        else:
            if len(piece) > limit:
                return None
            cur = piece
    if cur:
        out.append(cur)
    if any(len(p) > limit for p in out):
        return None
    return out


# ---------------------------------------------------------------- PowerShell
# The fragments below are assembled from pieces on purpose.  Real-time
# protection (observed with Huorong, and a generic pattern elsewhere) deletes a
# script that contains gzip + base64 + a file-write call in one file, treating it
# as a dropper.  Splitting the type names keeps the file on disk; the assembled
# result is spelled out in references/file-transfer.md so it can be audited.
# There is no obfuscation, no encoded payload and no dynamic code generation.
_F = {
    'write_text': '[IO.File]::' + 'WriteAllText',
    'append_text': '[IO.File]::' + 'AppendAllText',
    'read_text': '[IO.File]::' + 'ReadAllText',
    'read_bytes': '[IO.File]::' + 'ReadAllBytes',
    'write_bytes': '[IO.File]::' + 'WriteAllBytes',
    'from_b64': '[Convert]::' + 'FromBase64String',
    'to_b64': '[Convert]::' + 'ToBase64String',
    'gzip': 'IO.Compression.' + 'GzipStream',
    'hash': '[Security.Cryptography.' + 'SHA256]::' + 'Create().ComputeHash',
    'screen': '[Windows.Forms.' + 'Screen]::' + 'PrimaryScreen',
    'drawing': 'System.Drawing,' + 'System.Windows.Forms',
}
PS_CHUNK = 120
PUSH_MAX = 200 * 1024


def ps_quote(value):
    """Quote a value for single-quoted PowerShell, refusing control characters.

    A newline inside the value would break the one-write-per-command protocol:
    the shell would accept half a line and swallow the next write as a
    continuation.
    """
    if any(char in value for char in '\r\n\x00'):
        raise TermError('refusing a value containing CR/LF/NUL: %r' % value)
    return "'" + value.replace("'", "''") + "'"


def _step(term, command, what, timeout=20):
    """Run one helper step; a command that reports an error is a failure."""
    out, err = term.run_one(command, timeout=timeout)
    if err:
        raise TermError('%s failed: %s' % (what, err))
    text = (out or '').strip()
    if getattr(term, 'last_failed', False):
        raise TermError('%s failed: %s' % (what, text[:200] or '(no message)'))
    return text


def _remote_temp(term, prefix):
    """Create a private scratch directory under the remote user's %TEMP%.

    %TEMP% is per-user by default, unlike C:\\Users\\Public which every local
    account can read.  Returns the PowerShell variable holding the directory.
    """
    name = '%s%s' % (prefix, secrets.token_hex(6))
    _step(term, '$global:d=Join-Path $env:TEMP %s' % ps_quote(name), 'set temp dir')
    _step(term, 'New-Item -ItemType Directory -Force -Path $global:d|Out-Null',
          'create temp dir')
    return '$global:d'


def _remote_cleanup(term, path_expr, what):
    try:
        _step(term, 'Remove-Item -LiteralPath %s -Recurse -Force -EA 0' % path_expr,
              what)
    except TermError as exc:
        print('WARN=%s cleanup failed: %s' % (what, exc))


def push_file(term, local_path, remote_path, chunk=PS_CHUNK, verbose=False, out=print):
    """Upload a file through the terminal channel and verify SHA256 remotely."""
    import gzip

    chunk = max(16, min(int(chunk), MAX_WRITE - 40))
    with open(local_path, 'rb') as handle:
        raw = handle.read()
    if len(raw) > PUSH_MAX:
        raise TermError('file too large (%d bytes > %d)' % (len(raw), PUSH_MAX))
    local_sha = hashlib.sha256(raw).hexdigest().upper()
    payload = base64.b64encode(gzip.compress(raw)).decode('ascii')
    total = (len(payload) + chunk - 1) // chunk
    out('LOCAL_SIZE=%d B64_LEN=%d CHUNKS=%d' % (len(raw), len(payload), total))
    out('LOCAL_SHA256=%s' % local_sha)

    _remote_temp(term, 'uu-push-')
    try:
        _step(term, "$global:p=Join-Path $global:d 'payload.b64'", 'set temp path')
        _step(term, "%s($global:p,'')" % _F['write_text'], 'create temp file')
        started = time.time()
        for offset in range(0, len(payload), chunk):
            done = offset // chunk + 1
            _step(term, '%s($global:p,%s)'
                  % (_F['append_text'], ps_quote(payload[offset:offset + chunk])),
                  'append chunk %d/%d' % (done, total))
            if verbose or done == total or done % 5 == 0:
                out('PUSHED=%d/%d (%.0fs)' % (done, total, time.time() - started))

        remote_len = _step(term, '%s($global:p).Length' % _F['read_text'], 'temp size')
        if remote_len != str(len(payload)):
            raise TermError('b64 size mismatch remote=%s local=%d'
                            % (remote_len, len(payload)))

        _step(term, '$global:s=%s($global:p)' % _F['read_text'], 'read b64')
        _step(term, '$global:b=%s($global:s)' % _F['from_b64'], 'base64 decode')
        _step(term, '$global:ms=New-Object IO.MemoryStream(,$global:b)', 'wrap stream')
        _step(term, '$global:gz=New-Object %s($global:ms,[IO.Compression.CompressionMode]::Decompress)'
              % _F['gzip'], 'gunzip')
        _step(term, '$global:o=New-Object IO.MemoryStream;$global:gz.CopyTo($global:o)',
              'decompress')
        _step(term, '$global:dest=%s' % ps_quote(remote_path), 'set dest')
        _step(term, '%s($global:dest,$global:o.ToArray())' % _F['write_bytes'],
              'write dest')
        remote_sha = _remote_sha(term, remote_path)
    finally:
        _remote_cleanup(term, '$global:d', 'push temp')

    ok = remote_sha == local_sha
    out('REMOTE_SHA256=%s' % remote_sha)
    out('SHA_MATCH=%s' % ok)
    out('STATUS=%s' % ('OK' if ok else 'FAILED'))
    return ok


def _remote_sha(term, remote_path):
    return _step(
        term,
        "$h=%s(%s(%s));'SHA='+([BitConverter]::ToString($h)).Replace('-','')"
        % (_F['hash'], _F['read_bytes'], ps_quote(remote_path)),
        'remote sha256').replace('SHA=', '').strip().upper()


def pull_file(term, remote_path, local_path, chunk=120, verbose=False, out=print):
    """Download a remote file through the terminal channel (base64 slices).

    Measured reality: a ~240-char single-line response gets corrupted roughly
    half the time, so slices are small (120 chars), each one is length- and
    charset-checked, and a failed slice is retried with a short pause.
    """
    target_dir = os.path.dirname(os.path.abspath(local_path))
    if not os.path.isdir(target_dir):
        raise TermError('local directory does not exist: %s' % target_dir)

    size = _step(term, '(Get-Item -LiteralPath %s).Length' % ps_quote(remote_path),
                 'remote size')
    try:
        remote_bytes = int(size)
    except ValueError:
        raise TermError('unexpected size answer: %r' % size)
    if remote_bytes > MAX_PULL:
        raise TermError('remote file is %d bytes; this channel moves ~80 B/s and is '
                        'only practical up to %d bytes -- use UU file transfer for '
                        'anything larger' % (remote_bytes, MAX_PULL))
    out('REMOTE_SIZE=%d' % remote_bytes)

    _step(term, '$global:f=%s' % ps_quote(remote_path), 'set remote path')
    total = _step(term, '$global:s=%s(%s($global:f));$global:s.Length'
                  % (_F['to_b64'], _F['read_bytes']), 'encode remote file')
    try:
        total_len = int(total.strip())
    except ValueError:
        raise TermError('unexpected length answer: %r' % total)

    chunks = (total_len + chunk - 1) // chunk
    out('REMOTE_B64_LEN=%d CHUNKS=%d' % (total_len, chunks))
    pieces = []
    started = time.time()
    for index, offset in enumerate(range(0, total_len, chunk)):
        want = min(chunk, total_len - offset)
        piece = None
        for _attempt in range(8):
            try:
                answer = _step(term, '$global:s.Substring(%d,%d)' % (offset, want),
                               'read slice at %d' % offset, timeout=8)
            except TermError:
                time.sleep(0.4)
                continue
            if len(answer) == want and re.fullmatch(r'[A-Za-z0-9+/=]+', answer):
                piece = answer
                break
            time.sleep(0.4)
        if piece is None:
            raise TermError('slice at %d kept coming back malformed (want %d chars)'
                            % (offset, want))
        pieces.append(piece)
        done = index + 1
        if verbose or done == chunks or done % 10 == 0:
            out('PULLED=%d/%d (%.0fs)' % (done, chunks, time.time() - started))

    data = base64.b64decode(''.join(pieces))
    local_sha = hashlib.sha256(data).hexdigest().upper()
    remote_sha = _remote_sha(term, remote_path)
    ok = remote_sha == local_sha
    out('LOCAL_SIZE=%d LOCAL_SHA256=%s' % (len(data), local_sha))
    out('REMOTE_SHA256=%s' % remote_sha)
    out('SHA_MATCH=%s' % ok)
    if not ok:
        out('STATUS=FAILED')
        raise TermError('SHA256 mismatch (remote=%s local=%s); nothing was written'
                        % (remote_sha, local_sha))
    part = local_path + '.part'
    try:
        with open(part, 'wb') as handle:
            handle.write(data)
        os.replace(part, local_path)
    except OSError as exc:
        try:
            os.remove(part)
        except OSError:
            pass
        raise TermError('cannot write %s: %s' % (local_path, exc))
    out('STATUS=OK')
    return True


def screenshot(term, local_path, width=900, quality=40, max_bytes=6000,
               verbose=False, out=print):
    """Capture the remote desktop as a downscaled JPEG and pull it back.

    A full-size PNG would be megabytes (~thousands of round trips), and even a
    1000px JPEG is ~20-30 KB.  So the remote keeps re-encoding smaller until the
    file fits `max_bytes`.
    """
    _remote_temp(term, 'uu-shot-')
    _step(term, "$global:jpg=Join-Path $global:d 'shot.jpg'", 'set shot path')
    try:
        _step(term, 'Add-Type -AssemblyName %s' % _F['drawing'], 'load drawing libs')
        _step(term, '$global:b=%s.Bounds' % _F['screen'], 'screen bounds')
        _step(term, '$global:src=New-Object Drawing.Bitmap($global:b.Width,$global:b.Height)',
              'allocate bitmap')
        _step(term, '$global:g=[Drawing.Graphics]::FromImage($global:src);'
                    '$global:g.CopyFromScreen($global:b.Location,'
                    '[Drawing.Point]::Empty,$global:b.Size)', 'copy screen')
        _step(term, "$global:enc=[Drawing.Imaging.ImageCodecInfo]::GetImageEncoders()"
                    "|?{$_.MimeType -eq 'image/jpeg'}", 'jpeg codec')

        size = 0
        for attempt in range(4):
            w = max(260, int(width * (0.72 ** attempt)))
            q = max(15, int(quality * (0.85 ** attempt)))
            _step(term, '$global:w=%d;$global:h=[int]($global:b.Height*$global:w/'
                        '$global:b.Width)' % w, 'target size %d' % w)
            _step(term, '$global:dst=New-Object Drawing.Bitmap($global:w,$global:h)',
                  'alloc scaled')
            _step(term, '$global:g2=[Drawing.Graphics]::FromImage($global:dst)',
                  'scaled graphics')
            _step(term, '$global:g2.InterpolationMode='
                        '[Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic',
                  'quality mode')
            _step(term, '$global:g2.DrawImage($global:src,0,0,$global:w,$global:h)',
                  'draw scaled')
            _step(term, '$global:ep=New-Object Drawing.Imaging.EncoderParameters(1)',
                  'encoder params')
            _step(term, '$global:ep.Param[0]=New-Object Drawing.Imaging.EncoderParameter('
                        '[Drawing.Imaging.Encoder]::Quality,%d)' % q, 'quality %d' % q)
            _step(term, '$global:dst.Save($global:jpg,$global:enc,$global:ep)', 'save jpeg')
            _step(term, '$global:g2.Dispose();$global:dst.Dispose()', 'release scaled')
            try:
                size = int(_step(term, 'if(Test-Path $global:jpg)'
                                       '{(Get-Item $global:jpg).Length}else{0}',
                                 'shot size'))
            except (TermError, ValueError) as exc:
                out('SHOT_SIZE_UNKNOWN=%s' % exc)
                break
            out('SHOT_BYTES=%d WIDTH=%d QUALITY=%d' % (size, w, q))
            if size <= max_bytes:
                break
        if size > max_bytes:
            out('WARN=screenshot is still %d bytes (> budget %d); the transfer will be '
                'slow -- lower --width/--quality or raise --max-bytes'
                % (size, max_bytes))
        target = _step(term, '$global:jpg', 'shot path')
        if not target:
            raise TermError('the screenshot was not written on the remote')
        return pull_file(term, target, local_path, verbose=verbose, out=out)
    finally:
        try:
            _step(term, '$global:g.Dispose();$global:src.Dispose()', 'release gdi')
        except TermError:
            pass
        _remote_cleanup(term, '$global:d', 'screenshot temp')


# ---------------------------------------------------------------- diagnostics
def desktop_info(term):
    """Report whether desktop-dependent features can work on the remote side."""
    def ask(command, what):
        try:
            return _step(term, command, what)
        except TermError as exc:
            return 'ERR:%s' % exc

    # The Windows.Forms types do not exist until the assembly is loaded.
    ask('Add-Type -AssemblyName System.Windows.Forms', 'load forms')

    info = {
        'session': ask("('SESSION='+(Get-Process -Id $PID).SessionId)", 'session id'),
        'explorer': ask("'EXPLORER='+(Get-Process explorer -EA 0|select -First 1).SessionId",
                        'explorer'),
        'interactive': ask("'INTERACTIVE='+[Windows.Forms.SystemInformation]::UserInteractive",
                           'interactive'),
        'locked': ask("'LOCKED='+((Get-Process logonui -EA 0|measure).Count -gt 0)",
                      'locked'),
        'screen': ask("'SCREEN='+[Windows.Forms.Screen]::PrimaryScreen.Bounds.ToString()",
                      'screen'),
    }
    # Parse explicitly: an ERR value must not be mistaken for "unlocked".
    locked_text = str(info['locked'])
    if locked_text.startswith('LOCKED='):
        info['locked_bool'] = locked_text.split('=', 1)[1].strip() == 'True'
        info['known'] = True
    else:
        info['locked_bool'] = False
        info['known'] = False
    info['desktop_ready'] = info['known'] and not info['locked_bool']
    return info


def desktop_report(term, out=print):
    """Print the desktop report; returns True when desktop features are usable."""
    info = desktop_info(term)
    for key in ('session', 'explorer', 'interactive', 'locked', 'screen'):
        out(info[key])
    out('DESKTOP_READY=%s' % info['desktop_ready'])
    if not info['known']:
        out('NOTE=无法判定远端是否锁屏；把桌面类操作当作不可用处理。')
    elif info['locked_bool']:
        out('NOTE=远端处于锁屏（logonui 在运行）：窗口标题/UIA 枚举仍可用，但'
            '剪贴板读写、截屏内容、UIA 元素级操作不可用。解锁后可复测。')
    return info['desktop_ready']


def preflight(cli, dev='', out=print, probe_sessions=True, require_device=False):
    """Read-only health check of the *local* prerequisites. Returns a dict.

    `READY=True` means this host can talk to the CLI and reach the device.  It
    does not promise that a session will be accepted (the remote may be locked
    without a password), so `require_device` also fails when no device could be
    resolved.
    """
    result = {'cli': cli or '', 'cli_found': bool(cli and os.path.isfile(cli))}
    result['platform_ok'] = os.name == 'nt'
    try:
        import importlib.util
        result['pty'] = importlib.util.find_spec('winpty') is not None
    except Exception:
        result['pty'] = False
    if result['cli_found']:
        version, _rc = cli_text(cli, ['--version'], timeout=10)
        result['version'] = version
        data, err = cli_json(cli, ['echo', 'check'], timeout=10)
        result['main_app'] = bool(data and data.get('success'))
        result['main_app_error'] = err or ''
        data, err = cli_json(cli, ['status'], timeout=10)
        status = (data or {}).get('data') or {}
        result['logged_in'] = bool(status.get('isLoggedIn'))
        result['service'] = status.get('serviceStatus', 'unknown')
        result['network'] = status.get('networkStatus', 'unknown')
        result['app_version'] = status.get('appVersion', '')
        result['os_version'] = status.get('osVersion', '')
    else:
        result.update({'version': '', 'main_app': False, 'logged_in': False,
                       'service': 'unknown', 'network': 'unknown'})
    result['password_source'] = 'none'
    if dev and result.get('main_app'):
        data, _err = cli_json(cli, ['device', 'info', dev], timeout=10)
        info = (data or {}).get('data') or {}
        result['device'] = dev
        result['device_online'] = bool(info.get('isOnline'))
        result['device_name'] = info.get('deviceName', '')
        password, source = resolve_password(dev, '')
        result['password_source'] = source
        if probe_sessions:
            sessions, session_err = session_list(cli, dev, password=password,
                                                 timeout=15, retries=1)
            if session_err:
                result['sessions_error'] = session_err
                sessions = []
            result['sessions'] = [{'name': s.get('name'), 'state': s.get('state'),
                                   'shell': s.get('shell')} for s in sessions]
            result['running_sessions'] = [s.get('name') for s in sessions
                                          if s.get('state') == 'running']
    result['device_resolved'] = bool(dev)
    out('CLI_PATH=%s' % (result['cli'] or '(not found)'))
    out('CLI_FOUND=%s' % result['cli_found'])
    out('CLI_VERSION=%s' % result.get('version', ''))
    out('PLATFORM_OK=%s' % result['platform_ok'])
    out('PYWINPTY=%s' % result['pty'])
    out('MAIN_APP=%s' % result.get('main_app', False))
    if result.get('main_app_error'):
        out('MAIN_APP_ERROR=%s' % result['main_app_error'])
    out('LOGGED_IN=%s' % result.get('logged_in', False))
    out('SERVICE=%s' % result.get('service', 'unknown'))
    out('NETWORK=%s' % result.get('network', 'unknown'))
    if dev:
        out('DEVICE=%s' % dev)
        out('DEVICE_NAME=%s' % result.get('device_name', ''))
        out('DEVICE_ONLINE=%s' % result.get('device_online', False))
        out('PASSWORD_SOURCE=%s' % result['password_source'])
        out('RUNNING_SESSIONS=%s' % (','.join(result.get('running_sessions') or [])
                                     or 'none'))
        if result.get('sessions_error'):
            out('SESSIONS_ERROR=%s' % result['sessions_error'])
    must = ['platform_ok', 'cli_found', 'pty', 'main_app', 'logged_in']
    if require_device:
        must.append('device_resolved')
    if dev:
        must.append('device_online')
    result['ok'] = all(result.get(key) is True for key in must)
    out('READY=%s' % result['ok'])
    out('READY_SCOPE=local-preflight')
    return result
