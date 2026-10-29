# InfoRelay user guide

[简体中文](../zh/guide.md) · [Home](../../README.md) · [Configuration reference](configuration.md)

## Choose how to run it

| Mode | Best for | Prerequisites | When your computer is off |
|---|---|---|---|
| Local guided console | Trying subscriptions without editing JSON or learning GitHub | Python 3.9+, SMTP email account | Checks stop |
| GitHub Actions | Personal subscriptions that run in the cloud | GitHub account, SMTP account, one-time Secrets setup | GitHub continues scheduling |
| CLI / Docker | Developers, servers and NAS devices | Python or Docker on a running host | Depends on the host |

InfoRelay is software you run yourself, not an already-hosted subscription service. You still need your own email app password; local mode also needs Python. The wizard cannot create an email account or enable SMTP for you.

## Local console: download to first email

### 1. Download and launch

Use **Code → Download ZIP** in the repository, then unzip it. [Direct download](https://github.com/CHESHIRECAT-FINANCE/tibo-tweet-mail/archive/refs/heads/main.zip).

If Python is missing, install Python 3.9 or later from [python.org](https://www.python.org/downloads/). Prefer a currently supported version for a new installation. On Windows, select **Add Python to PATH**.

- **Windows:** double-click `start.bat` in the extracted folder.
- **macOS:** double-click `start.command`. If execution permissions prevent it from opening, open Terminal in that folder and run `python3 start.py`.
- **Linux:** run `python3 start.py` from the project folder. If venv support is missing, install your distribution’s Python venv package first.

The first launch installs the declared dependencies into the project’s `.venv`, leaving system Python packages alone. Once the launcher prints `http://127.0.0.1:8766/`, it opens the browser. Open that address manually if necessary.

The page should say **Local console**. If it says **Static setup guide**, you opened `web/index.html` or a static HTTP server. That version can generate cloud settings but cannot run local tasks.

### 2. Choose sources

The Python blog is provided as an editable example. Replace it, remove it, or add other sources. The three preset buttons add sources without replacing existing subscriptions.

- **RSS / Atom:** paste the feed URL published by the website, not its home page. For sites without a feed, try website monitoring.
- **Website changes:** compares extracted text from a public page. Changing clocks, advertisements or other page text may also trigger notifications. Prefer RSS for complex dynamic pages.
- **GitHub releases:** enter `owner/repository`, such as `cli/cli`. Ordinary commits, issues and bare tags are not included.
- **X:** enter an account name without `@`. This uses the public FxTwitter timeline and may fail when that service is unavailable.
- **Daily markets:** choose an hour. Exchange rates, oil and gold futures may be delayed; this is an informational digest.

Open **Filters and more settings** for keywords. Separate words with commas. Exclusions take precedence; an empty include list accepts all otherwise eligible items. The default first check records existing items without sending historical content. Explicitly choose the alternative to send current matches too.

Click **Next: connect email**.

### 3. Connect email

Choose QQ Mail, 163 Mail, Gmail or a custom provider. Presets fill in the SMTP host and TLS port. Enter the sending address, recipient and app password. The recipient initially follows the sender, but you can change it.

| Provider | Host / port | What you need |
|---|---|---|
| QQ Mail | `smtp.qq.com` / `465` | Enable SMTP and generate an authorization code; [official help](https://help.mail.qq.com/detail/106/985) |
| 163 Mail | `smtp.163.com` / `465` | Enable SMTP and use a client authorization password; [official help](https://help.mail.163.com/) |
| Gmail | `smtp.gmail.com` / `587` | An app password allowed by your account; normally requires two-step verification; [official help](https://support.google.com/accounts/answer/185833) |
| Custom | Your provider’s documented settings | SMTP username/password authentication and TLS |

An app password is different from your normal website password. Organization policy, account type or security settings can prevent app passwords; ask the account administrator or use another SMTP-capable account. OAuth email sign-in is not currently implemented.

Use **Server and time settings** to change the time zone. A market subscription’s day and due hour use that zone, while the existing quote formatter may label quote times in Beijing time.

### 4. Check and start

1. **Save & check:** saves settings on this computer and checks sources, SMTP connectivity, TLS and login. No email is sent.
2. **Preview:** shows up to five recent matching samples per source. No email is sent and delivery state is unchanged. This is not a pending-queue view.
3. **Send one test email:** sends exactly one requested test. Check your inbox and spam folder. “Submitted” means the email server accepted it, not that it reached the main inbox.
4. After verifying your mailbox, tick the automatic-delivery checkbox, choose an interval and click **Start automatic checks**. The first cycle starts immediately. Later cycles wait the selected interval after the previous cycle finishes.

The right panel shows runtime status, last completion and next check. A first RSS/GitHub/X cycle may send zero items because it establishes a baseline. A successfully fetched empty source establishes an empty baseline too, so its first future item is eligible for notification.

### Edit, pause and exit

Click **Stop checks** and wait for any active task before editing and saving. Stopping prevents future cycles; it does not cancel a message already being sent. The current task finishes and saves its records.

Reopening the console restores subscriptions and email settings. A blank password field with a “saved locally” hint keeps the existing password. Changing sender, host or port requires re-entering it. Exporting cloud settings also requires re-entering the password.

- Closing the browser tab leaves the launcher running.
- Closing the launcher or shutting down the computer stops local checks.
- Restarting restores settings but does not automatically send; start checks again in the console.
- Disabling an individual source pauses it and preserves pending items.

Private settings and state live in the project’s `.inforelay/` directory, which is ignored by Git. Keep it out of public repositories and shared folders. Stop checks before backing up the whole directory. On Windows, privacy also relies on your account and folder permissions.

## Run on GitHub Actions

This mode works without an always-on personal computer, but requires a one-time GitHub setup.

1. [Fork the repository](https://github.com/CHESHIRECAT-FINANCE/tibo-tweet-mail/fork). Do not commit email settings to the repository.
2. Use **Run on GitHub** in the console’s third step, or open `web/index.html` for the static wizard. Complete the form and click **Generate cloud settings**.
3. In your own repository, open **Settings → Secrets and variables → Actions → Secrets → New repository secret**, and create:

   | Name | Value |
   |---|---|
   | `INFO_RELAY_CONFIG` | The complete generated JSON |
   | `INFO_RELAY_STATE_KEY` | The generated encryption key |

4. Enable workflows on the **Actions** page if prompted after forking. Open **InfoRelay subscriptions → Run workflow**. First run `diagnose` and wait for success, then run `test-email` and verify receipt.
5. In **Settings → Secrets and variables → Actions → Variables**, add `INFO_RELAY_ENABLED` with the lowercase value `true`.
6. Manually run once in `run` mode. Check that processing and encrypted checkpoint saving succeeded, then allow scheduled runs to continue.

When editing subscriptions later, update `INFO_RELAY_CONFIG` and **keep the original key and `inforelay-state` branch**. Importing an existing configuration does not generate a replacement key. For a first deployment from an imported file, `python -m info_relay keygen` generates a new initial key.

Do not run independent local and cloud schedulers for the same subscriptions unless you intend to receive duplicate notifications. Local checkpoints are not automatically migrated to GitHub; the first cloud run creates its own baseline.

To stop scheduled cloud deliveries, change `INFO_RELAY_ENABLED` to `false`. A manually requested `run` still runs; select `diagnose` when you only want a connection check.

The workflow requests a check about every ten minutes. GitHub can delay or skip scheduled jobs, and inactive public repositories may have schedules disabled. See [schedule behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule). Standard public-repository runners are generally free; check the current [GitHub billing rules](https://docs.github.com/en/actions/concepts/billing-and-usage) for your account and runner.

## Server and Docker

After installing in a virtual environment:

```sh
python -m pip install .
inforelay init --config subscriptions.json
# Edit subscriptions.json, then:
inforelay diagnose --config subscriptions.json
inforelay watch --config subscriptions.json --state .inforelay/state.json --interval 600
```

`run` executes one cycle. `watch` waits the configured number of seconds after each completed cycle, without overlapping runs. SIGTERM asks it to finish the active cycle and exit. With cron, systemd or Windows Task Scheduler, invoke `run` and ensure all invocations share one state path.

For the included `Dockerfile` and `compose.yaml`, first download `subscriptions.private.json` from the wizard into the project root:

```sh
docker compose up --build -d
docker compose logs -f
docker compose stop
```

This starts the headless polling process. Configuration is mounted read-only and state is kept in the named `inforelay-data` volume. Do not delete the volume as a retry mechanism. The container does not expose a public web console.

To explore the pipeline without email credentials:

```sh
python -m info_relay preview --config examples/diy.json
python -m info_relay run --config examples/diy.json --state .inforelay/diy-state.json
```

Results are written to `outbox/` through the JSON-file destination. No email is sent.

## Troubleshooting

| Symptom | What to check |
|---|---|
| Python command not found | Install Python 3.9+; enable PATH on Windows and reopen the terminal |
| Dependency installation fails | Check network/PyPI access and venv support; rerun the launcher to retry incomplete installation |
| Port 8766 is already in use | Check whether the console is already running, or use `python -m info_relay serve --port 8767` |
| Page says “Static setup guide” | Launch `start.py` and open `127.0.0.1:8766`; a static server cannot run tasks |
| 403 / local service disconnected | Use the exact `127.0.0.1` address shown by the launcher, without a proxy or alternate hostname. Refresh after restarting the service |
| Email check fails | SMTP enabled? Correct app password, account, server, TLS port and network? |
| Submitted email is missing | Check spam, inbox rules, provider restrictions and the recipient address |
| Source fails | RSS home page instead of feed? Page requires login/JavaScript? X/GitHub/market provider down or rate-limited? |
| First successful run sends zero items | The default records a baseline. Wait for updates or explicitly opt into sending current items |
| Changing settings fails with pending items | Restore the original source settings, fix delivery and drain the queue before changing its URL or filters |
| Corrupt state / wrong encryption key | Stop delivery and restore a backup. Do not delete state or replace the key to bypass the error |
| Scheduled checks do not appear | Is the local launcher running? In GitHub, check the enable variable, workflow activation and queued runs |

When reporting an issue, include the version, run mode, error type and a redacted configuration structure. **Never include app passwords, encryption keys, complete private settings or unredacted screenshots.**
