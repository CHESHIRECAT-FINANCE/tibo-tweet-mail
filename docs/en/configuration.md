# Configuration reference

[简体中文](../configuration.md) · [Guide](guide.md) · [Extensions](architecture.md)

Configuration is JSON. Use a file through `--config`, the `INFO_RELAY_CONFIG` environment variable, or the default `subscriptions.json`. Files selected with `--config` take precedence. The console manages its own `.inforelay/subscriptions.json`.

## Top-level fields

| Field | Default | Meaning |
|---|---|---|
| `version` | `1` | Only schema version 1 is supported |
| `timezone` | `Asia/Shanghai` | IANA time zone |
| `batch_size` | `20` | 1–50 pending items per source per cycle |
| `sources` | Required | 1–20 source objects |
| `email` | `{}` | SMTP settings; required for email delivery, not for file/custom channels |
| `delivery` | `{"type":"email"}` | One registered destination, with optional `options` |

Unknown fields are rejected so misspellings cannot silently change behavior. There is no `plugins` or executable-code field in JSON.

## Common source fields

| Field | Default | Meaning |
|---|---|---|
| `id` | Required | Unique, stable identifier; 1–48 letters, digits, `_` or `-` |
| `name` | The ID | Display label, 1–80 characters |
| `type` | Required | Registered source type |
| `enabled` | `true` | False pauses fetching and delivery while preserving state |
| `keywords` | `[]` | Up to 30 include phrases, each 1–100 characters |
| `exclude` | `[]` | Up to 30 exclude phrases; exclusions win |
| `match` | `"any"` | `any` or `all` include-phrase matching |
| `initial` | `"skip"` | `skip` records a baseline; `send` also queues current matches |
| `processors` | None | Up to 10 ordered `{type, options}` processing steps |
| `options` | None | An object for a custom source’s own validated parameters |

Matching is case-insensitive substring matching across the title and retained body. Body text is limited to the first 10,000 characters. Website hashes cover the complete extracted text, but matching and notifications use the retained text, not only changed fragments.

## Built-in source parameters

- `rss`, `webpage`: `url`, a public HTTP(S) URL without credentials or special ports.
- `github`: `repository` in `owner/repository` format; `prereleases` defaults to false.
- `x`: `handle` without `@`; `reset_only=false`, `include_reposts=true`, `include_replies=false`, `include_quotes=false`.
- `market`: `hour`, integer 0–23, default 8. The day and due hour use the configuration time zone. Initial policy does not suppress the current day’s market digest.

## Email settings

```json
"email": {
  "smtp_host": "smtp.example.com",
  "smtp_port": 465,
  "smtp_user": "sender@example.com",
  "smtp_password": "YOUR_APP_PASSWORD",
  "to": "recipient@example.com"
}
```

Use port 465 for implicit TLS or your provider’s STARTTLS port, typically 587. Certificate verification remains enabled. If `smtp_password` is empty, `INFO_RELAY_SMTP_PASSWORD` is used. Do not commit real credentials. `check` validates the subscription shape; `diagnose` checks SMTP and sources without sending.

## Destinations and processors

```json
"delivery": {"type": "file", "options": {"directory": "outbox"}}
```

The default email destination uses `email` settings. The file destination needs no email account and writes one atomic JSON file per batch. A third-party channel uses its registered name and documented options. One destination is supported per configuration; multi-destination acknowledgement belongs inside a custom channel.

A source can contain:

```json
"processors": [{"type": "prefix", "options": {"text": "[Reading] "}}]
```

Processors run before keyword rules and preserve item IDs. Load third-party modules explicitly through `--plugin package.module` or `INFO_RELAY_PLUGINS`. [Contract details](architecture.md).

## State and migration

Default CLI state is `.inforelay/state.json`; override with `--state` or `INFO_RELAY_STATE_PATH`. `--storage sqlite --state .inforelay/state.sqlite3` selects SQLite for `run` / `watch`. SQLite cannot use encrypted Git checkpoint callbacks. The local console always uses JSON.

Keep source IDs stable when renaming. Changing identity-affecting settings re-baselines the source, but fails closed while it has pending content. A disabled or removed source retains historical state. Restore the original ID/settings to resume its queue.

For corruption, wrong cloud key, or capacity exhaustion, stop scheduling and restore/recover deliberately. Do not delete state to bypass errors. Cloud settings use `INFO_RELAY_STATE_KEY` and the `inforelay-state` branch; retain both. JSON/SQLite conversion and local/cloud migration are explicit operations, not automatic.

## Command map

| Command | Sends notifications? | Changes state? |
|---|---|---|
| `init`, `check`, `plugins`, `keygen` | No | `init` creates a new config only |
| `preview`, `diagnose` | No | No |
| `test-email` | One explicitly requested email | No delivery checkpoint |
| `run`, `watch` | Yes, if there are eligible pending items | Yes |
| `serve` | Only after explicit console actions | Saves settings / delivery state when requested |
| `cloud-restore`, `cloud-save` | No | Synchronizes encrypted JSON checkpoints |

`watch --interval` accepts 60–86400 seconds; default 600. The local console offers 10 minutes, 30 minutes or one hour. GitHub’s schedule is configured separately in its workflow.
