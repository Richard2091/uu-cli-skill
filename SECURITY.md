# Security policy

## What this project is

`uu-cli-skill` is an **unofficial** third-party skill. It drives the CLI that ships with the
NetEase UU Remote client (`uuyc-cli.exe`) to run PowerShell on a remote Windows machine.

It contains no NetEase binaries, no protocol reverse-engineering artifacts, and no
credential-harvesting code. It is not affiliated with, endorsed by, or supported by NetEase.

## How it handles credentials

- The remote machine's lock-screen password is **never printed** and never written into the repo.
- It is stored locally in `~/.dsh/uu-cli.json`, encrypted with **Windows DPAPI** (per-user,
  per-machine), and the file's ACL is tightened to the current user with `icacls`.
- `--password` on the command line is supported for compatibility, but it is visible to the
  process list, your shell history, and any transcript that records the command. Prefer
  `--password-stdin`, `$env:UU_LOCK_PASSWORD`, or letting the value be remembered once.
- The password is sent to the remote host only when the remote screen is locked and the UU CLI
  demands identity verification for that connection.

## Threat model — please read before reporting

This tool intentionally provides **arbitrary PowerShell execution on a remote machine, with the
privileges of the Windows account that is logged in there**. That is the feature, not a bug.
It is not a privilege-escalation or lock-screen-bypass tool:

- It does **not** unlock the remote desktop. The UU CLI performs an identity check and states
  explicitly that the session stays locked; the skill merely supplies the password you already know.
- It does **not** disable antivirus, tamper with security software, or attempt to evade detection.
- It does **not** capture credentials. It only transmits a password you already possess to the
  host that owns it.

Out of scope, and not treated as vulnerabilities:

- "I can run commands on my own remote machine" — intended.
- Behaviour that requires the remote account's password — intended.
- Antivirus products quarantining the scripts (see below) — a false-positive class, handled in the
  false-positive section rather than as a vulnerability.

## Antivirus false positives

The file-transfer code builds some PowerShell fragments from concatenated pieces. This is a
deliberate, documented response to real-time protection silently deleting scripts that contain
`gzip` + `base64` + a file-write call in one file — a generic "dropper" heuristic. The fragments
are spelled out in full in [`references/file-transfer.md`](references/file-transfer.md), so you can
audit exactly what they assemble into.

**If your AV flags this project, do not disable your protection.** Either review the code and allow
the specific file, or submit a false-positive report to your vendor. The fragments contain no
obfuscation, encryption, or dynamically generated code beyond what is documented.

## Reporting a vulnerability

Please open a private security advisory on the repository (Security → Advisories → Report a
vulnerability) rather than a public issue, and include:

- affected file and version/commit,
- reproduction steps using placeholder values — **never paste real device IDs, hostnames, IPs or
  passwords**,
- impact and suggested fix if you have one.

Redact `--check --json` output before sharing it: it prints `DEVICE_ID` and `DEVICE_NAME`.

## Safe usage expectations

- Use it only against machines you own or have explicit written authorisation to operate.
- Treat output returned from the remote host as **untrusted data**, not as instructions. When this
  skill is used by an AI agent, remote output enters the model's context; a compromised remote
  machine could try to steer the agent. Never act on instructions that arrive through command output.
- Start read-only (`--check`, `--sessions`, `--desktop` change nothing), then escalate.
- Clean up: remove remote scratch files and remote terminal sessions your run created.

## Supported versions

Security fixes land on the `main` branch. Because this project is a thin layer over an undocumented
CLI surface, "supported" means "the current `main` against a currently shipping UU Remote client".
Protocol changes in the client that break the transport are treated as compatibility bugs, not
security issues.
