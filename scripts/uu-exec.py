#!/usr/bin/env python3
"""
uu-exec.py - run commands on a remote Windows PC through `uuyc-cli term`.

SPDX-License-Identifier: MIT

The only entry point you need.  Nothing is hard-coded to one machine: the CLI is
discovered (argument > $UU_CLI_PATH > saved config > registry > PATH > common
install roots) and the device is resolved (argument > $UU_DEVICE_ID > saved
config > the single online Windows device).

Transport is hardened (see _uulib.py):
  * one write per command; success is proven by a fresh GUID/base64 marker, so
    bursty input delivery cannot produce a false "done"
  * PSReadLine is removed for the session (its line repainting corrupts the echo)
  * commands longer than one write are split on top-level ';' automatically
  * results travel base64, so the space-eating screen rendering cannot hurt them
  * a command that ran but failed is reported as a failure, not as success

Requires: pip install pywinpty   (see requirements.txt)

Examples:
  python uu-exec.py --check                          # preflight, no session
  python uu-exec.py "hostname"                       # one command
  python uu-exec.py --batch cmds.txt                 # many commands, one session
  python uu-exec.py --push local.txt C:\\tmp\\x.txt   # upload + SHA256
  python uu-exec.py --pull C:\\tmp\\x.txt local.txt   # download + SHA256
  python uu-exec.py --screenshot shot.jpg            # downscaled JPEG
  python uu-exec.py --desktop                        # can clipboard/UIA work?
  python uu-exec.py --sessions                       # list remote sessions
  python uu-exec.py --kill-all                       # clean our sessions

Exit codes: 0 ok / 1 setup, connection, login or usage problem / 2 remote command failed.
`--desktop` returns 1 when desktop features are unavailable (locked); that is a
diagnosis, not a connection failure.
"""
import os
import sys
import json
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _uulib as ul  # noqa: E402


def force_utf8():
    """Emit UTF-8 regardless of the console code page (Chinese names, paths)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass


force_utf8()

ACTIONS = (
    ('--check', lambda a: a.check),
    ('--forget', lambda a: a.forget),
    ('--sessions', lambda a: a.sessions),
    ('--kill', lambda a: bool(a.kill)),
    ('--kill-all', lambda a: a.kill_all),
    ('--batch', lambda a: bool(a.batch)),
    ('--push', lambda a: bool(a.push)),
    ('--pull', lambda a: bool(a.pull)),
    ('--screenshot', lambda a: bool(a.screenshot)),
    ('--desktop', lambda a: a.desktop),
    ('command', lambda a: bool(a.command)),
)


class Parser(argparse.ArgumentParser):
    """Usage errors are environment problems (exit 1), not remote failures.

    argparse defaults to exit code 2, which this tool reserves for "the remote
    command failed" -- callers could not tell a typo from a failed command.
    """

    def error(self, message):
        self.print_usage(sys.stderr)
        sys.stderr.write('%s: error: %s\n' % (self.prog, message))
        raise SystemExit(1)


class Reporter:
    """Collects human-readable lines, or buffers them for a single JSON object.

    `--json` promises machine-readable output for *every* action, so nothing may
    be printed directly while it is active.
    """

    def __init__(self, args):
        self.json = args.json
        self.lines = []

    @property
    def out(self):
        return self.lines.append if self.json else print

    def note(self, key, value):
        line = '%s=%s' % (key, value)
        if self.json:
            self.lines.append(line)
        else:
            print(line)

    def finish(self, payload=None):
        """Emit one JSON object combining `payload` with the buffered lines."""
        if not self.json:
            return
        body = dict(payload or {})
        body.setdefault('events', list(self.lines))
        print(json.dumps(body, ensure_ascii=False, default=str))


def emit(args, key, value):
    """Record a key=value line; buffered instead of printed under --json."""
    reporter = getattr(args, '_reporter', None)
    if args.json:
        if reporter is not None:
            reporter.lines.append('%s=%s' % (key, value))
        return
    print('%s=%s' % (key, value))


def fail(args, message, code=1):
    if args.json:
        reporter = getattr(args, '_reporter', None)
        body = {'ok': False, 'error': message}
        if reporter is not None:
            body['events'] = list(reporter.lines)
        print(json.dumps(body, ensure_ascii=False, default=str))
    else:
        print('ERROR=' + message)
    return code


def resolve_context(args):
    """Resolve CLI + device, recording how they were found. Raises TermError."""
    cli, source = ul.find_cli(args.cli)
    if not cli:
        raise ul.TermError(
            'uuyc-cli not found. Install UU Remote (GameViewer), or pass --cli PATH, '
            'or set %s. Looked in: argument, $%s, saved config, registry '
            'HKLM\\SOFTWARE\\Netease\\GameViewerSetup, PATH, common install roots.'
            % (ul.CLI_ENV, ul.CLI_ENV))
    args.cli = cli
    emit(args, 'CLI_PATH', cli)
    emit(args, 'CLI_SOURCE', source)
    device, dev_source = ul.resolve_device(cli, args.device)
    args.device = device
    emit(args, 'DEVICE_ID', device)
    emit(args, 'DEVICE_SOURCE', dev_source)
    return cli, device


def prepare_password(args):
    """Resolve the password once and record where it came from."""
    password, source = ul.resolve_password(args.device, args.password)
    args.password_source = source
    emit(args, 'PASSWORD_SOURCE', source)
    return password


def read_batch(path, text=None):
    if text is None:
        try:
            if path == '-':
                text = sys.stdin.read()
            else:
                with open(path, 'r', encoding='utf-8-sig') as handle:
                    text = handle.read()
        except OSError as exc:
            raise ul.TermError('cannot read batch file %s: %s' % (path, exc))
    commands = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        commands.append(line)
    return commands


# --------------------------------------------------------------------- actions
def cmd_check(args, reporter):
    cli, _source = ul.find_cli(args.cli)
    if cli:
        emit(args, 'CLI_PATH', cli)
    device = ''
    resolution_error = ''
    if cli and not args.no_device:
        try:
            device, dev_source = ul.resolve_device(cli, args.device, save=False)
            emit(args, 'DEVICE_ID', device)
            emit(args, 'DEVICE_SOURCE', dev_source)
        except ul.TermError as exc:
            resolution_error = str(exc)
            emit(args, 'DEVICE_RESOLVE', resolution_error)
    result = ul.preflight(cli, device, out=reporter.out,
                          require_device=not args.no_device)
    if resolution_error:
        result['device_resolve_error'] = resolution_error
        result['ok'] = False
    reporter.finish(dict(result, action='check'))
    return 0 if result['ok'] else 1


def cmd_forget(args, reporter):
    resolve_context(args)
    forgotten = int(bool(ul.forget_password(args.device)))
    emit(args, 'FORGOTTEN', forgotten)
    reporter.finish({'action': 'forget', 'ok': True, 'forgotten': forgotten})
    return 0


def cmd_sessions(args, reporter):
    cli, device = resolve_context(args)
    password = prepare_password(args)
    sessions, err = ul.session_list(cli, device, password=password)
    if err:
        return fail(args, 'cannot list sessions: %s' % err, 1)
    emit(args, 'SESSIONS', len(sessions) if sessions else 'none')
    for session in sessions:
        emit(args, '  session', '%-34s state=%-8s shell=%-10s created=%s' % (
            session.get('name', '?'), session.get('state', '?'),
            session.get('shell', '?'), session.get('createdAt', '?')))
    reporter.finish({'action': 'sessions', 'ok': True, 'device': device,
                     'sessions': sessions})
    return 0


def cmd_kill(args, reporter):
    cli, device = resolve_context(args)
    password = prepare_password(args)
    if args.kill_all:
        killed = ul.kill_our_sessions(cli, device, password=password,
                                      include_legacy=args.kill_legacy)
    else:
        killed = ul.kill_our_sessions(cli, device, password=password,
                                      names={args.kill})
        if not killed:
            killed = [(args.kill, ul.kill_session(cli, device, args.kill,
                                                  password=password))]
    for name, rc in killed:
        emit(args, 'KILLED', '%s rc=%s' % (name, rc))
    emit(args, 'KILLED_TOTAL', len(killed))
    ok = all(rc == 0 for _name, rc in killed)
    reporter.finish({'action': 'kill', 'ok': ok,
                     'killed': [{'name': n, 'rc': rc} for n, rc in killed]})
    return 0 if ok else 1


def login_hint(args):
    hints = {
        'none': 'the remote is locked and no password is available: pass --password, '
                '--password-stdin, set $UU_LOCK_PASSWORD, or run once with a password '
                'so it is remembered',
        'file': 'the remembered password was rejected: retry with --password, or clear '
                'it with --forget',
        'env': 'the password in $UU_LOCK_PASSWORD was rejected',
        'cli': 'the password from --password was rejected',
    }
    return hints.get(args.password_source, 'login failed')


def run_with_term(args, work):
    """Create one session, prepare it, hand it to `work`, always close it.

    There is no reliable way to reuse a session across invocations: a term
    session can be held by only one client, and after the holder goes away the
    `running` row in `term ls` is stale.  So every invocation owns its session
    and always ends it.
    """
    password = prepare_password(args)
    term = None
    try:
        term = ul.Term(cli=args.cli, dev=args.device, password=password,
                       shell=args.shell, verbose=args.verbose, name=args.session)
        if not term.logged_in:
            detail = getattr(term, 'login_error', '') or 'unknown reason'
            if args.password_source != 'none' and 'password' in detail.lower():
                return fail(args, '%s -- %s' % (login_hint(args), detail), 1)
            return fail(args, 'session setup failed: %s' % detail, 1)
        if (password and term.used_password and args.password_source in ('cli', 'env')
                and not args.no_save):
            # Only remember a password the remote actually accepted.  When the
            # desktop is unlocked it is never used, so storing it would record a
            # value that was never verified.
            if ul.save_password(args.device, password):
                emit(args, 'PASSWORD_REMEMBERED', 1)
        emit(args, 'SESSION_NAME', term.session_name)
        term.prepare()
        return work(term)
    finally:
        if term is not None:
            term.close()


def cmd_command(args, reporter):
    if args.batch:
        commands = read_batch(args.batch, getattr(args, '_batch_text', None))
        if not commands:
            return fail(args, 'batch file has no commands: %s' % args.batch, 1)
    elif args.command:
        commands = [args.command]
    else:
        return fail(args, 'no command given (pass one, use --batch FILE, or --stdin)', 1)

    results = []

    def work(term):
        for index, command in enumerate(commands, 1):
            out, err = term.run(command, timeout=args.timeout)
            failed = err is not None or getattr(term, 'last_failed', False)
            results.append({'index': index, 'command': command,
                            'output': (out or '').strip(), 'error': err,
                            'ok': not failed})
            if args.json:
                continue                    # reported as one object at the end
            if len(commands) > 1:
                print('--- [%d/%d] %s' % (index, len(commands), command))
            if err:
                # A transport failure means the session is broken; continuing
                # would burn retries*timeout for every remaining command.
                print('ERROR=' + err)
                print('STOPPED_AT=%d (transport)' % index)
                return 1
            print('EXIT_CODE=%d' % (1 if failed else 0))
            print('---OUTPUT---')
            print((out or '').rstrip())
            if failed and args.stop_on_error:
                print('STOPPED_AT=%d' % index)
                break
        return 2 if any(not r['ok'] for r in results) else 0

    code = run_with_term(args, work)
    reporter.finish({'action': 'command', 'ok': code == 0, 'device': args.device,
                     'exit': code, 'results': results})
    return code


def cmd_push(args, reporter):
    local, remote = args.push
    if not os.path.isfile(local):
        return fail(args, 'local file not found: %s' % local, 1)
    state = {}

    def work(term):
        state['ok'] = ul.push_file(term, local, remote, args.chunk, args.verbose,
                                   out=reporter.out)
        return 0 if state['ok'] else 2

    code = run_with_term(args, work)
    reporter.finish({'action': 'push', 'ok': bool(state.get('ok')),
                     'local': local, 'remote': remote, 'exit': code})
    return code


def cmd_pull(args, reporter):
    remote, local = args.pull
    state = {}

    def work(term):
        state['ok'] = ul.pull_file(term, remote, local, verbose=args.verbose,
                                   out=reporter.out)
        return 0 if state['ok'] else 2

    code = run_with_term(args, work)
    reporter.finish({'action': 'pull', 'ok': bool(state.get('ok')),
                     'remote': remote, 'local': local, 'exit': code})
    return code


def cmd_screenshot(args, reporter):
    state = {}

    def work(term):
        info = ul.desktop_info(term)
        if info['locked_bool']:
            emit(args, 'WARN', 'remote desktop is locked; the capture will only show '
                               'the lock screen')
        state['ok'] = ul.screenshot(term, args.screenshot, args.width, args.quality,
                                    args.max_bytes, args.verbose, out=reporter.out)
        return 0 if state['ok'] else 2

    code = run_with_term(args, work)
    reporter.finish({'action': 'screenshot', 'ok': bool(state.get('ok')),
                     'local': args.screenshot, 'exit': code})
    return code


def cmd_desktop(args, reporter):
    state = {}

    def work(term):
        state['ready'] = ul.desktop_report(term, out=reporter.out)
        return 0 if state['ready'] else 1

    code = run_with_term(args, work)
    reporter.finish({'action': 'desktop', 'ok': bool(state.get('ready')),
                     'desktop_ready': bool(state.get('ready')), 'exit': code})
    return code


# ----------------------------------------------------------------------- parser
def chunk_size(text):
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError('--chunk must be an integer')
    if not 16 <= value <= 200:
        raise argparse.ArgumentTypeError('--chunk must be between 16 and 200')
    return value


def build_parser():
    parser = Parser(
        description='UU CLI remote executor (uuyc-cli term over a hardened PTY).',
        epilog='Run --check first; it is read-only and needs no session.')
    parser.add_argument('command', nargs='?', default='',
                        help='command to run remotely')
    parser.add_argument('--cli', default='', help='path to uuyc-cli.exe (auto-detected)')
    parser.add_argument('--device', default='',
                        help='device id or name (auto-detected when unambiguous)')
    parser.add_argument('--password', default='',
                        help='locked-screen password; prefer --password-stdin, since '
                             'argv is visible in the process list and shell history')
    parser.add_argument('--password-stdin', action='store_true',
                        help='read the locked-screen password from stdin')
    parser.add_argument('--no-save', action='store_true',
                        help='do not remember a password that the remote accepted')
    parser.add_argument('--forget', action='store_true',
                        help='forget the saved password for this device')
    parser.add_argument('--shell', default='powershell',
                        help='remote shell (only powershell is supported)')
    parser.add_argument('--timeout', type=int, default=20,
                        help='per-command result timeout in seconds (20); raise it for '
                             'slow commands')
    parser.add_argument('--batch', metavar='FILE',
                        help="file of commands to run in one session ('-' for stdin)")
    parser.add_argument('--stop-on-error', action='store_true',
                        help='stop a batch at the first failing command')
    parser.add_argument('--stdin', action='store_true',
                        help='read a single command from stdin')
    parser.add_argument('--session', default='',
                        help='name for the new remote session')
    parser.add_argument('--push', nargs=2, metavar=('LOCAL', 'REMOTE'),
                        help='upload a file and verify SHA256')
    parser.add_argument('--pull', nargs=2, metavar=('REMOTE', 'LOCAL'),
                        help='download a file and verify SHA256')
    parser.add_argument('--screenshot', metavar='LOCAL',
                        help='capture the remote desktop to a local JPEG')
    parser.add_argument('--width', type=int, default=900,
                        help='screenshot width in pixels before downscaling (900)')
    parser.add_argument('--quality', type=int, default=40,
                        help='JPEG quality 1-100 (40)')
    parser.add_argument('--max-bytes', type=int, default=6000,
                        help='screenshot transfer budget in bytes (6000)')
    parser.add_argument('--chunk', type=chunk_size, default=ul.PS_CHUNK,
                        help='push chunk size in characters, 16-200 (120)')
    parser.add_argument('--check', action='store_true',
                        help='read-only preflight (no session created)')
    parser.add_argument('--no-device', action='store_true',
                        help='with --check: check the local side only')
    parser.add_argument('--desktop', action='store_true',
                        help='report whether clipboard/screenshot/UIA content can work')
    parser.add_argument('--sessions', action='store_true',
                        help='list remote terminal sessions')
    parser.add_argument('--kill-all', action='store_true',
                        help='kill the sessions this tool created')
    parser.add_argument('--kill-legacy', action='store_true',
                        help='with --kill-all: also kill q/p/c-prefixed sessions left '
                             'by version 0.2 and earlier')
    parser.add_argument('--kill', default='', help='kill one session by name')
    parser.add_argument('--json', action='store_true',
                        help='machine-readable output (one JSON object)')
    parser.add_argument('--verbose', action='store_true')
    return parser


def _dispatch(args):
    if args.check:
        return cmd_check(args, args._reporter)
    if args.forget:
        return cmd_forget(args, args._reporter)
    if args.sessions:
        return cmd_sessions(args, args._reporter)
    if args.kill or args.kill_all:
        return cmd_kill(args, args._reporter)

    resolve_context(args)
    if args.push:
        return cmd_push(args, args._reporter)
    if args.pull:
        return cmd_pull(args, args._reporter)
    if args.screenshot:
        return cmd_screenshot(args, args._reporter)
    if args.desktop:
        return cmd_desktop(args, args._reporter)
    return cmd_command(args, args._reporter)


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.password_source = 'none'
    args._batch_text = None

    if os.name != 'nt':
        print('ERROR=uu-cli runs on Windows only (uuyc-cli.exe + ConPTY); this host '
              'is %s' % sys.platform)
        return 1

    chosen = [name for name, test in ACTIONS if test(args)]
    if len(chosen) > 1:
        print('ERROR=choose exactly one action, got: %s' % ', '.join(chosen))
        return 1
    if not chosen:
        parser.print_help(sys.stderr)
        return 1

    if args.password_stdin:
        # The password is the first line of stdin; anything after it belongs to
        # --stdin / --batch -.  Reading it here keeps it out of argv, the process
        # list and shell history.
        if args.stdin or args.batch == '-':
            first, _sep, rest = sys.stdin.read().partition('\n')
            args.password = first.strip()
            if args.stdin:
                args.command = rest.strip()
                args.stdin = False
            else:
                args._batch_text = rest
        else:
            args.password = sys.stdin.readline().rstrip('\r\n')
    elif args.password and not args.json:
        sys.stderr.write('WARN=--password is visible in the process list and shell '
                         'history; prefer --password-stdin\n')

    args._reporter = Reporter(args)
    try:
        return _dispatch(args)
    except ul.TermError as exc:
        return fail(args, str(exc), 1)
    except KeyboardInterrupt:
        return fail(args, 'interrupted by user', 1)
    except OSError as exc:
        return fail(args, 'local I/O error: %s' % exc, 1)


if __name__ == '__main__':
    sys.exit(main())
