# Tibo notices and daily market mail

Checks a public X timeline for usage-reset notices and emails them through SMTP.
Also sends a daily RMB/USD, crude oil and gold futures digest after 08:00 Asia/Shanghai,
with retries until noon. Market data is from Yahoo Finance and may be delayed.

## GitHub Actions

The workflow runs on GitHub-hosted Linux every five minutes. Your computer can be off.
GitHub schedules are best effort and can be delayed or dropped during high load.

1. Store the full configuration JSON (based on `config.example.json`) in the repository
   Actions secret `TIBO_MAIL_CONFIG`. Use a Gmail app password for Gmail SMTP.
2. For migration, optionally set `TIBO_INITIAL_STATE` to a JSON object containing
   `state.json` and `market-state.json` from the existing local installation.
   This is used only when the `mail-state` branch does not exist.
3. Set the repository Actions variable `MAIL_ENABLED` to `true`, then run the workflow
   manually and check the result. Scheduled runs use the default branch.
4. After cloud execution is verified, disable the local LaunchAgent to avoid duplicate mail.

Only source code and delivery checkpoints (public post IDs and the last digest date)
are public. Credentials and recipient addresses belong in Secrets, never in source files.
The `mail-state` branch stores checkpoints between runners. Do not delete it: recreating
it can replay an old bootstrap checkpoint. Runs are serialized; state restoration or
write-permission failures stop mail delivery. A failed source still saves other successful
deliveries. SMTP delivery and Git persistence are not atomic: a runner interruption or
a failed push just after sending can cause a retry to duplicate a message.

Without an initial checkpoint, the first successful timeline fetch only records existing
posts; it does not email historical reset notices. The daily digest may still be sent if due.
Only the latest 20 timeline entries are fetched, so a long outage can miss older posts.

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
python3 tibo_tweet_mail.py preview
python3 tibo_tweet_mail.py run
```

On macOS, `install` installs the local five-minute LaunchAgent and `uninstall` removes it.
