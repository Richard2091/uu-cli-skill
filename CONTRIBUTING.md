# Contributing

Thanks for taking a look. This project is a thin layer over an **undocumented CLI surface**, so most
contributions are about measurement and robustness rather than features.

## Before you start

- Read [`SKILL.md`](SKILL.md) and [`references/terminal-pty.md`](references/terminal-pty.md). The
  channel has genuinely surprising behaviour, and several "obvious improvements" have already been
  tried and reverted.
- Read [`SECURITY.md`](SECURITY.md). This tool runs arbitrary PowerShell on a remote machine; changes
  that widen that surface need a clear justification.

## Ground rules

1. **Never commit real identifiers.** No device IDs, machine names, IP addresses, account names,
   passwords, or `--check --json` dumps from a real account. Use `<DEVICE_ID>`, `<DEVICE_NAME>`,
   `CONTROLLER-PC`, `TARGET-PC`.
2. **Never weaken the safety rails.** Don't broaden session-kill matching, don't store credentials in
   plaintext, don't add code that disables or evades security software.
3. **Keep the transport honest.** Success must keep requiring both signals (echo + fresh marker), and
   command failure must keep being detected from ErrorRecord, not `$?`.
4. **One change per pull request**, with the reasoning in the description.

## What a good change looks like

- **Bug report**: what you ran, what you expected, what happened, and the relevant `ERROR=` /
  `EXIT_CODE=` output with identifiers redacted.
- **Behaviour change**: include the measurement. This project documents observed behaviour, so a
  claim like "writes of N characters are safe" needs the experiment that produced it.
- **New capability**: state which environments it was verified on (Windows version, UU client
  version, PowerShell version, locked/unlocked remote).

## Testing

There is no automated test suite — most of this cannot be tested without a second Windows machine and
a live UU account. Before opening a pull request, at minimum run:

```powershell
python scripts/uu-exec.py --check                 # preflight
python scripts/uu-exec.py "hostname"              # one command
python scripts/uu-exec.py --check --json          # must parse as one JSON object
powershell -NoProfile -File scripts/uu-doctor.ps1 # PowerShell 5.1 path
```

If your change touches the transport, also verify:

- a failing command reports `EXIT_CODE=1` (not 0);
- `-EA 0` probes are **not** reported as failures;
- after the run, `--sessions` reports `SESSIONS=none` (no stale rows);
- a deliberate mid-transfer failure leaves no `%TEMP%\uu-*` directory on the remote.

## Code style

- Python 3.9 compatible. No 3.10+ syntax, no new third-party dependencies.
- Code comments explain **why**, especially where the code looks odd. Several constructs exist only
  because the channel misbehaves in a specific way; if you remove one, say why it is no longer
  needed.
- User-facing messages should be actionable: say what failed and what to try.
- Errors go through `TermError` so the CLI can print `ERROR=` instead of a traceback.

## Documentation

- Keep [`references/uu-exec-reference.md`](references/uu-exec-reference.md) in sync with the parser in
  `scripts/uu-exec.py`. If you add a flag, document it and its default.
- If you change a documented behaviour, update both `SKILL.md` and the relevant reference.
- Update [`CHANGELOG.md`](CHANGELOG.md).

## Reporting security issues

Do not open a public issue. See [`SECURITY.md`](SECURITY.md).
