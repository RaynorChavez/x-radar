# Authenticate the collector manually

X Radar never automates sign-in or stores credentials in source control. Create
an isolated persistent Firefox profile and perform the X login yourself.

From the repository root on a machine with a graphical session:

```bash
mkdir -p var/firefox-profile
firefox --no-remote --profile "$PWD/var/firefox-profile"
```

Sign in to X, confirm that the Home timeline loads, and close Firefox cleanly.
The profile should now contain `var/firefox-profile/cookies.sqlite`.

Verify the profile with a bounded read-only capture:

```bash
python3 collector/firefox_collect.py \
  --target https://x.com/home \
  --output var/inbox/auth-check.json \
  --limit 3 \
  --max-scrolls 1 \
  --max-minutes 2
```

The collector reports `AUTH_REQUIRED` when the session is missing or expired.
Reopen the same profile and sign in manually again. Never email, upload, commit,
or share the profile or its cookie database.
