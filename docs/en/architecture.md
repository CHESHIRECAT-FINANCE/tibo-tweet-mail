# Architecture and extension guide

[简体中文](../zh/architecture.md) · [User guide](guide.md) · [Configuration](configuration.md)

InfoRelay is a small, single-writer pipeline with replaceable boundaries. The console, CLI and GitHub workflow all call the same engine. Version 0.2 provides a practical extension foundation, not a distributed scheduler or a multi-user SaaS backend.

## Modules and responsibilities

| Module | Owns | Does not own |
|---|---|---|
| `config.py` | Validation, defaults, references to registered plugins | Importing arbitrary code from JSON |
| `plugins.py` / `builtins.py` | Versioned contracts, explicit registration, built-in adapters | Installing third-party packages |
| `sources.py` / `net.py` | Public source parsing and bounded requests | Scheduling or delivery state |
| `engine.py` | Baselines, filtering, durable queue, independent source reports | UI, transport-specific authentication |
| `delivery.py` | Binding a channel and stable batch IDs | Removing pending items before success |
| `storage.py` / `state.py` | Exclusive lease, checkpoint schema, atomic JSON or SQLite storage | Distributed consensus |
| `cloud.py` | Encrypted Git checkpoint synchronization | Local console settings |
| `service.py` / `server.py` | Local operations, non-overlapping scheduler, loopback UI/API | Public internet hosting |
| `web/` | Bilingual beginner flow and cloud configuration export | Loading Python plugins |

```mermaid
sequenceDiagram
    participant Entry as Console / CLI / workflow
    participant Engine
    participant Store
    participant Source
    participant Channel
    Entry->>Engine: run(validated_config)
    Engine->>Store: acquire exclusive lease; load and validate
    Engine->>Store: prove persistence works
    Engine->>Source: fetch independently (up to 4 workers)
    Source-->>Engine: bounded, normalized items
    Engine->>Engine: processors, keyword rules, deduplication
    Engine->>Store: persist pending snapshot
    Engine->>Channel: submit one source batch with stable ID
    Channel-->>Engine: success or exception
    Engine->>Store: acknowledge success, or preserve pending
    Engine-->>Entry: per-source reports
```

The cloud callback pushes encrypted snapshots before submission and after acceptance. Persistence failures stop further delivery. A source failure does not stop other sources, and an existing queue can be retried even when its source is temporarily unavailable.

## Extension contracts (API version 1)

Import the contracts from `info_relay.plugins`. Register against a `Registry` instance; built-ins use the same interface.

### Sources

`SourcePlugin(fetch, validate, mode="stream")`

- `validate(source)` validates and may normalize its source settings. Keep extension-specific fields under `source["options"]`.
- `fetch(source, now)` returns a list of at most 1,000 normalized items. `now` is timezone-aware. Raise on network, authentication or parsing failures; a successful empty list is a valid empty baseline.
- Modes: `stream` deduplicates stable item IDs; `snapshot` tracks each content transition; `daily` uses the engine’s once-per-local-day gate and must use `item(now.date().isoformat(), ...)` for its one daily item. A daily adapter returns `[]` before its due hour.
- Fetch calls may run concurrently across sources. Keep adapters stateless or provide thread-safe internals. Use bounded network timeouts and payloads.

Use `info_relay.sources.item(identity, title, body, url="", published="")`. It hashes the stable identity and normalizes text. The runtime validates the exact fields:

| Field | Contract |
|---|---|
| `id` | 64-character lowercase SHA-256 hex string |
| `title` | Nonempty plain text, max 300 characters, no control characters |
| `body` | Plain text, max 10,000 characters |
| `url` | Empty or a supported public HTTP(S) URL, max 2,000 characters |
| `published` | Plain text, max 100 characters |

Do not derive a stream identity from a changing title if the provider gives you a stable item ID. Website snapshots should use the complete extracted text as their identity, even though the retained body is bounded.

### Processors

`ProcessorPlugin(process, validate)`

`process(item, options)` receives a copy and returns a valid item or `None` to discard it. Preserve its ID. Processors run in configured order before keyword matching. Dropped stream items are still remembered, so they are not reconsidered every cycle. Processor exceptions preserve the original source checkpoint and report a failure.

A built-in `prefix` processor demonstrates an ordinary transformation:

```json
"processors": [{"type": "prefix", "options": {"text": "[Reading] "}}]
```

Changes to a pipeline participate in the source fingerprint. With pending items, a changed fingerprint fails closed until you restore the original settings and drain the queue.

### Notification channels

`ChannelPlugin(send, validate, diagnose=None)`

`send(options, subject, body, batch_id)` returns only after the destination durably accepts the batch; otherwise raise. The engine retains pending items on any exception. `batch_id` is stable for a source and ordered batch of item IDs, so a destination can make retries idempotent. If you change batch size, batch boundaries and IDs can change; item-level deduplication is stronger where available.

`validate(options)` must not send. An optional `diagnose(options)` checks connectivity without submitting a notification. Built-in destinations are `email` and `file`. The file destination atomically writes `<batch_id>.json`, making identical batch retries overwrite the same file.

The engine uses one destination per configuration. To deliver to several systems, implement a channel that owns durable acknowledgement per destination. A naive fan-out that retries all destinations after one failure can duplicate deliveries.

### Checkpoint stores

Implement `CheckpointStore` from `info_relay.storage`:

```python
class MyStore:
    def load(self) -> dict: ...
    def save(self, data: dict) -> None: ...
    def lease(self): ...  # context manager: exclusive for the entire cycle
```

- Return the schema accepted by `state.checked`; store full pending snapshots, not just IDs.
- `save` must be atomic and durable, enforce size limits, and raise on failure.
- The exclusive lease spans fetch, delivery and acknowledgement. A transaction around the save operation alone is insufficient to prevent duplicate concurrent submissions.
- Corrupt data must fail closed. Never silently return an empty checkpoint after corruption.
- Default `JsonStore` uses atomic replace and fsync; `SQLiteStore` stores the same schema in a transactional SQLite row. Local file leases coordinate processes on the same machine; they are not distributed locks.
- The encrypted Git callback is specific to JSON snapshots. A custom store owns its persistence and cannot be combined with that callback.

```sh
python -m info_relay run --config examples/diy.json \
  --storage sqlite --state .inforelay/diy.sqlite3
```

JSON and SQLite states do not migrate automatically. Stop the old scheduler and intentionally create a new baseline, or implement a controlled export/import using the validated schema.

## A runnable plugin

The repository includes [`examples/custom_plugin.py`](../../examples/custom_plugin.py), which registers all three plugin types:

```python
INFO_RELAY_API = 1

def register(registry):
    registry.source("json_feed", SourcePlugin(fetch_feed, validate_feed))
    registry.processor("min_words", ProcessorPlugin(enough_words, validate_words))
    registry.channel("markdown", ChannelPlugin(write_markdown, validate_directory))
```

From the repository root:

```sh
python -m info_relay plugins --plugin examples.custom_plugin
python -m info_relay check --config examples/plugin.json --plugin examples.custom_plugin
# Replace the example.com URL with a public JSON array you control, then:
python -m info_relay preview --config examples/plugin.json --plugin examples.custom_plugin
```

The JSON source expects `[{"id":"1","title":"Example","body":"At least five words of useful text","url":"https://example.com/post"}]`. `examples/plugin.json` is a schema example, not a live feed. The `examples/diy.json` RSS-to-file example is immediately runnable over the network.

Install a third-party module in your environment before loading it. You can also set `INFO_RELAY_PLUGINS=package.module,another.module`. There is no automatic discovery, remote code download or import path in the subscription JSON. Registration is transactional and name collisions are rejected. Plugins are trusted Python code and are not sandboxed.

## Embed the engine

```python
from info_relay import config, engine
from info_relay.plugins import Registry
from info_relay.builtins import register
from info_relay.storage import SQLiteStore

registry = Registry()
register(registry)
# Register your own extensions here.
cfg = config.load("examples/diy.json", registry)
reports = engine.run(cfg, registry=registry,
                     store=SQLiteStore(".inforelay/embedded.sqlite3"))
```

`engine.run` also accepts `now`, `fetcher` and the legacy SMTP `sender` callable for controlled tests. Custom channels should use the registry. Tests must use fixtures and simulated transports rather than real recipients.

## Packaging, deployment and development

- Source launcher: `python start.py` creates a project-local environment and opens the console.
- Installable package: `python -m pip install .` exposes `inforelay`. This release is installed from source or its built wheel; do not assume a public PyPI package has been published.
- Build a wheel: `python -m pip wheel --no-deps . -w dist`. Static console assets and the example config are included.
- Headless: `inforelay watch ...`, Docker Compose, or a system scheduler invoking `run`.
- GitHub: opt-in workflow with independent encrypted state. Keep the original legacy mail workflow separate.

```sh
python -m pip install -r requirements.txt
python -m unittest -v
node --check web/app.js
node test_web_model.js
```

The test suite exercises failed delivery, corrupt state, capacity limits, source transitions, explicit plugin loading, custom channels, SQLite, the console’s request boundary and scheduler lifecycle. CI runs the full suite on Python 3.9/3.13 on Linux, and extension/console tests on Windows.

## Compatibility and upgrades

- The subscription JSON and checkpoint schema remain `version: 1`. Existing built-in v0.1 source fingerprints are preserved when no new options are added.
- Plugin API version 1 is explicit. An incompatible contract change will use a new API version and documented migration; implementation helpers not listed here are not public extension contracts.
- While the project is 0.x, read the changelog before upgrading. A tested extension boundary is not a promise that every third-party plugin is safe or compatible.
- Stop scheduling, back up private configuration and state, upgrade code/dependencies, run `check` and `diagnose`, then resume. Preserve source IDs and encryption keys.
- Changing URL, filters, processors or destination with pending items requires a deliberate migration. Restore old settings and drain the queue first. Credentials can be updated to repair delivery without discarding pending content.

## Security and operational boundaries

The local server uses an explicit static-asset allowlist, loopback binding, exact Host/Origin validation, a per-process session token, bounded JSON requests, no CORS access, and no credential-bearing request logs. Saved SMTP passwords are redacted from its read API. It is not designed to be exposed by a tunnel or reverse proxy.

Built-in source requests reject local/private addresses and validate redirects; source content is never executed as code or HTML. Extensions can bypass these helpers, so the plugin author is responsible for equivalent limits. Keep private files and exports out of source control. See [SECURITY.md](../../SECURITY.md).
