# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- The README is split by language: `README.md` is English-only and `README.zh-CN.md` holds the
  Chinese documentation, each linking to the other at the top.

## [0.3.0]

First public release. The transport had been working privately for a while; this release is mostly
about making it correct, portable and safe for someone who has never seen the machine it was built on.

### Added

- `--batch FILE` to run many commands in one session (saves 12–18 s of session setup per command).
- `--json` for every action, emitting a single parseable JSON object.
- `--password-stdin`, so the remote password never has to appear in argv, the process list, or shell
  history.
- `--timeout`, `--session`, `--kill`, `--kill-legacy`, `--stdin`, `--no-device`.
- `references/uu-exec-reference.md`: full parameter table, output fields, JSON shapes, exit codes.
- `references/system-ops.md`: process, service, port and filesystem recipes.
- `README.md`, `SECURITY.md`, `CONTRIBUTING.md`, `LICENSE` (MIT), `requirements.txt`, `.gitignore`.
- Automatic discovery of the CLI (argument → env → config → registry → `PATH` → common roots) and of
  the device (argument → env → config → the single online Windows device).

### Changed

- **Command failure is now reported as failure.** The helper previously sampled `$?` after the
  invocation had already been followed by an assignment, so `$?` was always true and *every* failed
  command reported `EXIT_CODE=0`. Failure is now decided from ErrorRecord presence and encoded in
  the marker (`@guid@0|1@base64#`); deliberate `-EA 0` probes still count as success.
- Configuration moved from `~/.dsh/uu-cli.json` to `%APPDATA%\uu-cli\config.json`, and passwords are
  stored with **Windows DPAPI** instead of plaintext (the old file is migrated and deleted).
- File ACL is tightened with `icacls`; `os.chmod(0o600)` does nothing to a Windows DACL.
- Remote scratch files moved from `C:\Users\Public` (readable by every local account) to a private
  `%TEMP%\uu-*` directory, cleaned up in a `finally` block so a failed transfer leaves nothing behind.
- Session names are `uu-YYYYMMDD-HHMMSS-xxxx` instead of `uu<5 digits>`, so two concurrent runs
  cannot collide.
- `--kill-all` matches only names this tool generates. The old `uu/q/p/c` prefix test would also have
  killed unrelated sessions such as `core`, `prod` or `python`.
- `Term.close()` waits for the local PTY to exit before killing the remote session, and warns instead
  of silently swallowing a failed cleanup.
- `--check` no longer writes configuration (it previously saved the auto-detected device), and its
  `READY` result now fails when the device cannot be resolved.
- Usage errors exit `1`, not `2`; `2` is reserved for remote command failure.
- `--desktop` reports `DESKTOP_READY=False` when the lock state cannot be determined, instead of
  optimistically assuming "unlocked".
- Pull refuses files above 64 KB, writes through a `.part` file, and leaves no corrupt output when the
  SHA256 check fails.

### Fixed

- `pywinpty` receives an argv **list**; passing a string made it shlex-split a quoted path with spaces
  and fail with `FileNotFoundError`.
- `term ls`/`kill`/`has`/`rename` require the account password on a locked remote; it is now piped on
  stdin instead of spinning a whole PTY.
- CLI stdout and stderr are no longer concatenated before JSON parsing (progress lines on stderr made
  `json.loads` fail with `Extra data`); parsing now uses `raw_decode` to skip leading noise.
- A `success: false` envelope is treated as an error rather than an empty result.
- Two helper definitions are no longer written back to back; a half-written definition made every
  subsequent command report "no marker" and looked like a broken channel.
- `split_command` is quote- and brace-aware, so `'C:\a;b.txt'` and `if/else` bodies are not cut in half.
- `--sessions`, `--kill`, `--kill-all` no longer raise a traceback when the CLI or device cannot be
  resolved.
- `--chunk 0` no longer raises `ZeroDivisionError`; values outside 16–200 are rejected.
- A missing `--batch` file, or a `--pull` target directory that does not exist, produce a clear error
  instead of a traceback.
- `uu-doctor.ps1` works under Windows PowerShell 5.1: native stderr is separated from stdout before
  JSON parsing (5.1 wraps it in ErrorRecords and rendered them into the JSON), and the file is saved
  as UTF-8 with BOM so its non-ASCII match strings survive.
- `uu-doctor.ps1` counts sessions from `items` rather than an optional `total`, matches `127` only as
  the "remotely controlled" message, and prints `READY=` so both entry points agree.
- Rate limiting ("操作过于频繁") is retried with backoff and reported as throttling, not as a rejected
  password.
- Login failures now report the real cause (no password / throttled / controlled elsewhere / protocol
  version / session already attached).
- `--desktop` loads `System.Windows.Forms` before using its types, so `INTERACTIVE` and `SCREEN`
  probes no longer fail with "type not found".
- A password is only remembered when the remote actually used and accepted it.

### Removed

- `--keep` and `--attach`. A term session can be held by only one client, and after the holder goes
  away the `running` row in `term ls` is stale, so `attach` either fails with
  `already attached (code 2001)` or `terminal session not found`. Cross-invocation reuse is not
  possible; every invocation owns its session and uses `--batch` for multiple commands.

## [0.2.0] - internal

- Remember the remote lock-screen password after it is accepted; `--password`, `$UU_LOCK_PASSWORD`,
  `--no-save`, `--forget`.
- `--desktop` diagnostic.
- Rewrote the executor around a persistent PTY session: one write per command, dual success signal,
  base64 results, `$global:` for cross-command state.
- Dropped `PSReadLine` for the session.

## [0.1.0] - internal

- Initial transport: PTY via `pywinpty`, ANSI stripping, prompt detection, session cleanup,
  push/pull over the terminal channel.
