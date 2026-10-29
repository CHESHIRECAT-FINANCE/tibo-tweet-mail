# Changelog

## 0.2.0 — 2026-10-30

- Added a Chinese/English three-step console with email presets, local configuration persistence, source preview, connection checks, requested test email, and start/stop scheduling.
- Added explicit version-1 Source, Processor and Channel extension contracts. Built-in adapters share the registry; plugin loading is opt-in and transactional.
- Added a file destination, title-prefix processor, SQLite checkpoint store and a runnable custom JSON/processor/Markdown plugin example.
- Added `serve`, `watch`, `plugins`, a package entry point, wheel assets, launchers for Windows/macOS/Linux and headless Docker configuration.
- Added bilingual project introductions, beginner guides, architecture guides and configuration reference.
- Preserved v1 configuration/checkpoint formats and default built-in source fingerprints. Legacy Tibo mail settings remain separate.

Upgrade: stop scheduling, back up private settings/state, update dependencies, run checks, then resume. The console does not auto-start deliveries after restarting. Changing state backend does not migrate existing checkpoints automatically. Keep cloud encryption keys unchanged.

## 0.1.0 — 2026-10-30

- Initial public release: RSS/Atom, website text, GitHub Releases, X and daily market sources.
- Added keyword filters, independent durable queues, encrypted GitHub checkpoints and a static configuration generator.
- Fixed empty-source initialization so the first future item is eligible for notification.
