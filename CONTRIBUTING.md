# Contributing / 参与贡献

Thank you for improving InfoRelay. Start with the [architecture guide](docs/en/architecture.md) or [中文架构指南](docs/zh/architecture.md).

## Development

```sh
python -m venv .venv
# Activate the environment, then:
python -m pip install -r requirements.txt
python -m unittest -v
node --check web/app.js
node test_web_model.js
python -m info_relay serve --no-browser
```

Tests use fixtures, mocked SMTP and temporary state. Do not send messages to real recipients in tests. Do not commit private settings, credentials, checkpoint databases, generated outbox files or local `.deploy` artifacts.

## Changes we can review

- New source, processor or destination: use the public contracts, validate options and add fixture-based tests. Demonstrate failure behavior and stable identities, not just a successful response.
- Storage changes: preserve atomic writes, bounded snapshots, fail-closed corruption handling and the whole-cycle exclusive lease.
- User-facing changes: keep Chinese and English flows and guides in sync. Check keyboard navigation and a narrow viewport. Explain prerequisites and recovery steps accurately.
- Compatibility changes: update the changelog, migration instructions and plugin API/schema version where required.

A PR should describe the user-visible problem, resulting behavior and validation. Keep unrelated changes separate. Start an issue for major new dependencies or a change to delivery semantics. We do not promise support for private scraping, paywall bypasses or publicly exposed console servers.

安全问题请按 [SECURITY.md](SECURITY.md) 处理；普通问题请提供可复现步骤和脱敏信息，不要粘贴授权码或完整配置。
