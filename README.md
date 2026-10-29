# Tibo notices and daily market mail

Checks a public X timeline for usage-reset notices and emails them through SMTP.
Also sends a daily RMB/USD, crude oil and gold futures digest after 08:00 Asia/Shanghai,
with catch-up retries until midnight (configurable). Market data is from Yahoo Finance and may be delayed.

## GitHub Actions

The workflow runs on GitHub-hosted Linux every five minutes. Your computer can be off.
GitHub schedules are best effort and can be delayed or dropped during high load.

1. Store the full configuration JSON (based on `config.example.json`) in the repository
   Actions secret `TIBO_MAIL_CONFIG`. Use a Gmail app password for Gmail SMTP.
2. For migration, optionally set `TIBO_INITIAL_STATE` to a JSON object containing
   `state.json` and `market-state.json` from the existing local installation.
   This is used only when the `mail-state` branch does not exist.
3. Run the workflow manually with `diagnose=true`. It checks configuration, SMTP
   authentication and both data sources without sending mail. Then use `send_test=true`
   to explicitly request a single test email; it does not also send normal notifications.
4. Set the repository Actions variable `MAIL_ENABLED` to `true`, then run the workflow
   with both options off. Confirm the checkpoint restore/save and individual delivery
   steps succeed. Scheduled runs use the default branch.
5. After cloud execution is verified, disable the local LaunchAgent to avoid duplicate mail.

Only source code and public delivery checkpoints are published: post IDs, the last
digest date, and public text/author/timestamp snapshots of queued reset notices.
Credentials and recipient addresses belong in Secrets, never in source files or checkpoints.
The `mail-state` branch stores checkpoints between runners. Do not delete it: recreating
it can replay an old bootstrap checkpoint. Runs are serialized; state restoration or
write-permission failures stop mail delivery. Unexpected tracked files stop state publication.
The market checkpoint is saved before notices run; if that save fails, notices stop.
A failed source still saves other successful deliveries. SMTP delivery and Git persistence
are not atomic: a runner interruption or
a failed push just after sending can cause a retry to duplicate a message.

Without an initial checkpoint, the first successful timeline fetch only records existing
posts; it does not email historical reset notices. The daily digest may still be sent if due.
Only the latest 20 timeline entries are fetched, so a long outage can miss posts never
fetched. Once a notice is queued, its snapshot survives SMTP failures and disappears
from the queue only after successful submission, even if it has left the source window.
Queued notices are also retried when the latest timeline request fails.

Deploy all updated Python files and workflows together. Older versions do not understand
the pending queue and may discard it; drain queued notices before reverting to old code.

Standard GitHub-hosted runners are free for public repositories. In public repositories,
GitHub can disable schedules after 60 days without repository activity. Delivery-checkpoint
commits provide activity when state changes, but monitor the Actions page for failures or
disabled schedules. Never put untrusted pull-request code in the mail workflow.

Sources: [billing](https://docs.github.com/en/actions/concepts/billing-and-usage),
[schedules](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

## Local use

Python 3.9+; standard library only. Place configuration in
`~/.config/tibo-tweet-mail/config.json` or provide `TIBO_MAIL_CONFIG` in the environment.
`TIBO_STATE_DIR` overrides the default `~/.local/share/tibo-tweet-mail` state directory.

```sh
python3 -m unittest -v
python3 tibo_tweet_mail.py check-config
python3 tibo_tweet_mail.py diagnose
python3 tibo_tweet_mail.py preview
python3 tibo_tweet_mail.py run
```

On macOS, `install` installs the local five-minute LaunchAgent and `uninstall` removes it.
Both commands recognize an existing installation's label for this script, including legacy
labels. Multiple matching installations require an explicit `TIBO_LAUNCH_AGENT_LABEL`.

## Delivery and diagnostics

- `diagnose` tests SMTP connection, verified TLS and authentication without submitting a
  message. It also checks available local checkpoints and both data sources. On a fresh
  cloud diagnostic runner no checkpoints are restored or changed; production restore
  validates the persisted branch separately. Successful SMTP submission is not proof
  of arrival in the inbox; check spam/filtering if the delivery log reports success.
- `run-market` and `run-notices` isolate the two sources. Actions reports their outcomes
  separately and saves successful checkpoints even if another source fails. When mail is
  disabled, a manual run defaults to diagnostics instead of succeeding without doing work.
- Public HTTP GETs retry up to three times with bounded backoff and a 15-second socket
  timeout. This includes FxTwitter's intermittent 404s, connection errors, 429 and
  transient server errors and empty responses. `Retry-After` seconds and HTTP dates are
  respected; requests that require waiting over 30 seconds are deferred to a later run,
  instead of retrying early. Permanent errors and invalid data remain visible failures.
- SMTP retries connection failures only before message submission. Credentials and TLS
  certificate failures stop immediately. A dropped connection during submission has an
  unknown outcome, so it is not retried within that run. A failed QUIT after acceptance
  does not turn a successful delivery into a failure. A later run may still duplicate
  an ambiguously accepted message: SMTP has no end-to-end exactly-once guarantee.
- `market_digest_hour` defaults to 8 and `market_digest_end_hour` to 24, in Shanghai time.
  A delayed run can send today's digest in the afternoon/evening, once per day. Set the
  end hour to 12 to restore the previous noon deadline. Missed past days are not replayed.
- `smtp_port=465` uses verified implicit TLS; other ports require STARTTLS. `to` and
  `smtp_user` accept one plain email address each. Boolean options must be JSON booleans,
  ports/hours must be integers. Gmail app-password grouping spaces are removed; spaces
  in passwords for other SMTP providers are preserved.
- Replies and quotes remain controlled by their flags even for reposts. Duplicate or
  malformed source IDs cannot silently corrupt checkpoints; an empty initial timeline
  is not marked seeded. State files are replaced atomically after flushing to disk.

GitHub's cron expression is a request, not a five-minute delivery guarantee. In the
2026-10-29 investigation, observed scheduled runs were hours apart. Catch-up reduces
missed daily digests but cannot guarantee prompt notices or recover posts never fetched
before they left the latest 20 entries. For a strict timing requirement, use an always-on
scheduler with the same checkpoint discipline and disable the other scheduler.

## Verified incident on 2026-10-29

- [22:42 Shanghai run](https://github.com/CHESHIRECAT-FINANCE/tibo-tweet-mail/actions/runs/40894215314)
  failed while fetching the FxTwitter timeline: HTTP 404. Configuration validation,
  checkpoint restore and checkpoint save succeeded; this was not an SMTP rejection.
- [09:57 Shanghai run](https://github.com/CHESHIRECAT-FINANCE/tibo-tweet-mail/actions/runs/40803454762)
  logged a successful daily digest submission, and the cloud checkpoint recorded
  `sent_date: 2026-10-29`.
- The local LaunchAgent was not loaded, and its last log entry was 2026-10-28 14:25.
  Cloud execution was active. Do not re-enable the local scheduler while cloud delivery
  is enabled, because the two installations have independent checkpoints.

Implementation reference: [Python SMTP behavior](https://docs.python.org/3/library/smtplib.html).
