# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Developers, DevOps engineers, systems administrators, and power users managing git repositories across multiple hosting services (GitHub, GitLab, Gitea, Bitbucket, self-hosted forges, and local bare repositories).

## Product Purpose

`gitsync` delivers automated, resilient multi-platform repository mirroring, synchronization, health monitoring, and disaster recovery. It provides a headless CLI engine, a curses-based TUI, and a native operator web dashboard with zero runtime dependencies. Success means repositories stay in sync across forges with verifiable audit trails, zero data loss, and immediate visibility into platform drift or token expiration.

## Positioning

`gitsync` is the zero-dependency, single-binary-like git sync operator: no Docker required (though supported), no pip dependencies, no Node/npm build steps, using only the Python standard library. It unifies mirroring, drift detection, star syncing, and scheduled background operations under one deterministic lock-guarded architecture.

## Operating Context

- **Local Machine**: Interactive CLI and terminal TUI (`curses`) over SSH or local terminal.
- **Background Daemon**: Systemd user/system service and timer for automated periodic runs.
- **LAN / Server**: Localhost or 0.0.0.0 bound HTTP operator dashboard (`gitsync web`) with token and password PBKDF2 authentication, CSRF guards, and DNS-rebinding protection.

## Capabilities and Constraints

- **Engine**: Python 3.8+ stdlib only (`http.server`, `urllib.request`, `subprocess`, JSON).
- **Frontend**: Vanilla ES6, HTML5 semantic markup, CSS3 custom properties; zero runtime JS or CSS dependencies.
- **Supported Platforms**: GitHub, GitLab, Gitea, Bitbucket, Local bare git repositories, and generic git remotes.
- **Core Modes**: `safe` (fast-forward only) and `full` (mirroring with force-push and tag pruning).
- **Locking**: Exclusive OS-level file locking (`sync.lock`) prevents overlapping sync runs.

## Brand Commitments

- **Name**: `gitsync`
- **Tone**: Pragmatic, technical, dependable, transparent.
- **Identity**: Dark-themed operator aesthetic (`#0d1117`), high contrast, no decorative fluff, no artificial AI glow or side-tab clichés.

## Evidence on Hand

- Verified across 182 automated test cases (177 unit/logic tests + 5 Playwright end-to-end browser tests).
- Clean code architecture verified through continuous master checklist security, lint, and schema audits.

## Product Principles

1. **Zero External Dependencies**: Standard library Python and vanilla web assets only; never introduce pip, npm, or CDN dependencies into the runtime.
2. **Operational Safety First**: Safe defaults, non-destructive sync by default, robust lock acquisition, and explicit confirmations for mirror overwriting or branch pruning.
3. **Rock-Solid Stability**: Every number uses tabular numerals, every heading balances text wrap, and all background jobs stream live progress deterministically.
4. **Cross-Platform Parity**: Full functionality across Linux, macOS, and Windows.
