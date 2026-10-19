# Gmail cleanup

A small Python script that:

1. moves messages carrying provider spam headers (`X-Spam-Flag: YES` or
   `X-Spam-Status: Yes`), or matching your own sender/subject rules, into the
   account's Spam folder and marks them read;
2. marks every remaining unread message in the chosen folders as read.

It talks IMAP to `imap.gmail.com`, prints a summary, and writes an audit log of
every message it touched.

> **Important Gmail caveat:** Gmail does not expose its own spam score through
> IMAP. Gmail-classified spam already lives in `[Gmail]/Spam`; messages in your
> inbox normally have no `X-Spam-*` headers. So the header-based spam step will
> usually find **0 messages** on Gmail. That is expected, not a bug. Use the
> `--spam-from` / `--spam-subject` / `--spam-contains` rules below to move
> specific inbox mail to Spam. See
> [Why the spam step may find nothing](#why-the-spam-step-may-find-nothing).

## Requirements

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) — installs and runs everything else

```bash
uv sync
```

If `uv` was installed to `~/.local/bin` and is not on your `PATH` yet:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## Quick start (app password)

An app password is the fastest option. It is **not** your normal Google
password.

1. Turn on 2-Step Verification:
   <https://myaccount.google.com/security>
2. Create an app password:
   <https://myaccount.google.com/apppasswords>
3. Run the script and follow the prompts:

```bash
uv run python cleanup_mail.py
```

It asks for:

```
Email address: you@gmail.com
App password for you@gmail.com:
```

Then shows a preview and asks:

```
Proceed? [Y/n]:
```

Press Enter and it cleans `INBOX`.

### Storing credentials instead of typing them

Copy `.env.example` to `.env` and fill it in:

```dotenv
EMAIL_ADDRESS=you@gmail.com
EMAIL_APP_PASSWORD=xxxxxxxxxxxxxxxx
```

`.env` is already in `.gitignore`. With a `.env` present, the script does not
prompt for either value.

## Browser sign-in with Google OAuth

This avoids storing an app password, but needs a one-time Google Cloud setup.

1. Go to <https://console.cloud.google.com/> and create (or select) a project.
2. **APIs & Services > Library**, search for **Gmail API**, enable it.
3. **APIs & Services > OAuth consent screen**:
   - choose **External**,
   - fill in the required fields,
   - add your own Google address under **Test users**.
4. **APIs & Services > Credentials > Create credentials > OAuth client ID**:
   - application type: **Desktop app**,
   - download the JSON.
5. Save the JSON next to the script as `credentials.json`.
6. Install the helper package and run:

```bash
uv sync
uv run python cleanup_mail.py --auth oauth
```

Your default browser opens. Pick your Google account and approve access. The
resulting token is cached in `token.json` (also git-ignored), so later runs do
not reopen the browser. Use `--reauth` to force a fresh sign-in.

Account scopes requested: `https://mail.google.com/` (IMAP access only; the
script does not use any other Google API).

## Usage

```bash
uv run python cleanup_mail.py                  # prompts, then cleans INBOX
uv run python cleanup_mail.py --dry-run        # preview only, change nothing
uv run python cleanup_mail.py --auth oauth     # browser sign-in
uv run python cleanup_mail.py --all-folders    # every folder except Spam/Trash/Sent/Drafts/All Mail
uv run python cleanup_mail.py --folder INBOX --folder "Receipts"
uv run python cleanup_mail.py --limit 25       # at most 25 per folder per action
uv run python cleanup_mail.py --yes            # skip the [Y/n] confirmation
uv run python cleanup_mail.py --list-folders   # show folder names and flags
uv run python cleanup_mail.py --no-spam        # only mark unread as read
uv run python cleanup_mail.py --no-unread      # only move spam
```

Useful flags:

| Flag | Meaning |
| --- | --- |
| `--dry-run` | Show the plan and exit without changing anything. |
| `--limit N` | Cap each action in each folder (good for a first real run). |
| `--yes` | Skip the confirmation prompt. |
| `--sample N` | Number of example messages to print (default 5, `0` disables). |
| `--folder NAME` | Folder to clean; repeatable. Default: `INBOX`. |
| `--all-folders` | Use every selectable folder except system/spam folders. |
| `--spam-folder NAME` | Override the auto-detected Spam/Junk folder. |
| `--spam-from TEXT` | Move mail whose `From` header contains `TEXT` (repeatable). |
| `--spam-subject TEXT` | Move mail whose `Subject` contains `TEXT` (repeatable). |
| `--spam-contains TEXT` | Move mail whose `From` or `Subject` contains `TEXT` (repeatable). |
| `--auth MODE` | `auto` (default), `app-password`, or `oauth`. |
| `--reauth` | Ignore the cached OAuth token and sign in again. |
| `--audit-log PATH` | Audit CSV location (default `cleanup-audit.csv`). |

## Move mail that matches your own rules

Gmail already separates its own spam. To move specific inbox mail to Spam,
tell the script what to look for:

```bash
# Preview first, always
uv run python cleanup_mail.py --dry-run \
  --spam-from newsletter@example.com \
  --spam-subject "You won"

# Then apply
uv run python cleanup_mail.py \
  --spam-from newsletter@example.com \
  --spam-subject "You won" \
  --spam-contains "crypto giveaway"
```

Rules are:

- case-insensitive **substring** matches (not regex),
- checked against the real `From` and `Subject` headers (server search is
  verified locally, so a loose server match cannot move the wrong mail),
- combined with OR: any matching rule moves the message,
- repeatable: pass a flag more than once to add more patterns.

`--spam-from` checks only `From`, `--spam-subject` only `Subject`, and
`--spam-contains` checks both. Matched messages are marked read and moved to
Spam, exactly like header-detected spam, and are recorded in the audit log as
`mark_read_for_spam` + `move_to_spam`. `--limit` still caps how many messages
are moved per folder.

With no rules, behaviour is unchanged: only provider `X-Spam-*` headers are
used.

## Count messages and find your first sent email

`mailbox_stats.py` is read-only: it opens every folder with `EXAMINE` and never
changes flags or moves messages.

```bash
uv run python mailbox_stats.py --auth oauth
uv run python mailbox_stats.py --auth oauth --json
uv run python mailbox_stats.py --auth oauth --oldest 10
```

It reports message and unread counts for All Mail, INBOX, Sent Mail, Drafts,
Spam, and Trash. It also prints your oldest sent and received messages with
their date, counterpart, subject, and Message-ID.

"Oldest" means the earliest `Date:` header — the date Gmail displays for the
message. Server `INTERNALDATE` is only a fallback for malformed headers. This
matters for imported or migrated mail, where the server date can be much newer
than the actual send/receive date. Received messages are found in All Mail and
exclude anything whose `From:` is your own address. If your oldest message was
deleted from the mailbox, no tool can find it.

Use `--oldest N` to list more than the default 5 examples per direction.

## Optional: local LLM spam classification

Gmail does not expose spam scores over IMAP, so `cleanup_mail.py` can only act
on provider headers. If you want a real content classifier, `llm_spam_scan.py`
uses a local model through [Ollama](https://ollama.com/) — no email leaves the
machine.

Install Ollama and a model:

```bash
sudo pacman -S ollama
sudo systemctl enable --now ollama
ollama pull qwen3.5:4b
```

`qwen3.5:4b` (~3.3 GB) is the default: it fits comfortably in 6 GB of VRAM and
is roughly 3x faster than an 8B model. `qwen3:8b` (~5.2 GB) is the
higher-quality option if you have the time and VRAM headroom.

Scan first, with no changes:

```bash
uv run python llm_spam_scan.py --auth oauth --limit 20
uv run python llm_spam_scan.py --auth oauth --snippet-chars 600
```

Then apply only high-confidence spam:

```bash
uv run python llm_spam_scan.py --auth oauth --apply --threshold 0.95 --yes
```

How it handles context:

- One email per request: one system prompt plus one email, no chat history.
  Every message gets a fresh context.
- `num_ctx` defaults to 2048 and inputs are capped: headers plus at most
  `--snippet-chars` of body text.
- It checks Ollama's `prompt_eval_count`; at ≥95% of `num_ctx` it retries
  without the snippet instead of trusting a squeezed prompt.
- `done_reason != "stop"` is treated as an error, not as a verdict.
- Verdicts are cached by Message-ID in `llm-spam-cache.json`, so re-runs are
  free and consistent.
- Only `label=spam` with `confidence >= --threshold` is moved. Everything else
  stays put. `--allow allow.txt` forces senders/domains to ham.

Recommended first run: use `--limit 25` without `--apply`, inspect
`llm-spam-verdicts.csv`, then apply.

### Benchmark

`benchmark_spam.py` generates a labeled synthetic mailbox (500 spam + 150 ham
by default), classifies it with the same per-email code path, and reports
accuracy, precision, recall, F1, and false-positive rate:

```bash
uv run python benchmark_spam.py --spam 500 --ham 150
uv run python benchmark_spam.py --limit 50        # quick sanity check
```

For the adversarial set (1,000 hard examples by default when you pass 500/500):

```bash
uv run python benchmark_spam.py --hard --spam 500 --ham 500
```

The hard set mixes pass-authentication spam (lookalike domains, compromised
accounts, gift-card fraud, invoice/document lures) with legitimate mail that
looks spammy (urgent promotions, "free", money, newsletters) and ham that fails
authentication because it was forwarded.

Outputs `spam-benchmark-emails.csv` and `spam-benchmark-results.csv`, including
false positives and false negatives for review. A `*-meta.json` records wall
time, emails/second, and average/p95 latency.

Results are flushed per message, so an interrupted run can still be scored.
To compare models, including partial runs:

```bash
uv run python report_results.py \
  spam-benchmark-4b-results.csv \
  spam-benchmark-8b-results.csv --detail
```

`report_results.py` prints accuracy, precision, recall, F1, false-positive rate,
and the miss rate (spam classified as ham).

## Two-pass triage of a large mailbox (read-only)

`run_triage.sh` orchestrates a resumable two-pass run. It **never moves,
deletes, or flags mail** — it writes JSONL files only.

### Simple 10k run

```bash
./run_10k.sh
```

It asks for your email address, prints every message and model verdict live,
and writes the same log to `logs.txt`. It uses `triage-10k/` as the output
directory, so running it again resumes where it stopped. Set `LIMIT=500
OUT_DIR=triage-pilot ./run_10k.sh` for a pilot. `Ctrl-C` stops it safely.

### Classification policy

- **All publicity is spam**: marketing, advertising, sales, newsletters,
  promotions, and brand updates — even if opted in and even with an
  unsubscribe link.
- **Personal, work, and human mail is ham.**
- **Transactional/automated mail is ham**: receipts, shipping confirmations,
  invoices, statements, appointments, security alerts, and password resets.
- Senders listed in `allow.txt` are forced to ham before the model runs.
  Matching is case-insensitive against From and Reply-To. `allow.txt` is local
  and git-ignored; the default contains `Generation`.

```bash
# Pilot first: 500 messages, separate output directory, no mailbox changes.
LIMIT=500 ./run_triage.sh

# If the pilot looks sane, run the full inbox.
OUT_DIR=triage ./run_triage.sh
```

Phases:

| Phase | Output |
|---|---|
| `export` | `mailbox.jsonl` — From/Subject/Date/auth/snippet, via `EXAMINE` |
| `pass-a` | `pass-a.jsonl` — classifier prompt v2 |
| `pass-b` | `pass-b.jsonl` — independent “intent” prompt |
| `merge` | `spam.jsonl`, `ham.jsonl`, `review.jsonl`, `errors.jsonl`, `summary.json` |

Only messages where both passes agree go into `spam.jsonl` / `ham.jsonl`.
Disagreements, missing results, and failures go to `review.jsonl` /
`errors.jsonl`. Nothing is applied to the mailbox.

### Optional pass C (disagreements only)

`pass-c` re-checks **only** the A/B disagreements with a different judge prompt.
It defaults to `qwen3.5:4b` (`PASS_C_MODEL`). At the default `--c-threshold
0.90`, the 4B judge often abstains on hard disagreements; those stay in
`review.jsonl` for manual review. Anything it does resolve goes to
`review-resolved.jsonl`, never directly into `spam.jsonl` / `ham.jsonl`.

```bash
./run_triage.sh pass-c
./run_triage.sh merge --c-threshold 0.90
```

Do not lower the threshold just to get answers: on the hard fake set, all of
the 4B judge's correct resolutions were at 0.85, and its wrong ones were at
0.75–0.85. Abstaining is the safer behavior.

Useful settings:

```bash
PASS_A_MODEL=qwen3.5:4b PASS_B_MODEL=qwen3.5:4b ./run_triage.sh
SNIPPET_CHARS=600 FOLDERS="INBOX" ./run_triage.sh
MAX_ERROR_RATE=0.01 ./run_triage.sh

# Per-message logging: sender, subject, verdict, confidence, latency, reason
VERBOSE=1 LOG_FILE=logs.txt ./run_triage.sh
```

`VERBOSE=1` logs every exported message and every model verdict in the form:

```text
[pass-a] verdict=SPAM conf=0.95 | phishing attempt with lookalike domain | INBOX:123 | sender@example | Subject
```

Those logs contain message metadata and model reasons. They stay local:
`logs.txt` and `logs/` are git-ignored. Do not publish them.

The run is resumable: completed phases are marked with `.done`, and each pass
skips keys already present. `FORCE=1` ignores `.done` markers but keeps the
data. A pilot automatically uses `OUT_DIR=triage-pilot-<LIMIT>` so it cannot
contaminate the full run.

Expected runtime for 40,000 emails on the 4B model: **~8 hours per pass**,
about **16 hours for both**, plus export. Manual review of `review.jsonl` is
the real bottleneck if the disagreement rate is high.

## Safety

- A summary is printed before anything changes and you confirm with `[Y/n]`.
- `--dry-run` never writes.
- `--limit` lets you try the script on a handful of messages first.
- Every action is appended to `cleanup-audit.csv`:

  ```csv
  timestamp,action,folder,folder_raw,uid
  2026-10-19T11:29:30+02:00,mark_read,INBOX,INBOX,1
  2026-10-19T11:29:30+02:00,mark_read_for_spam,INBOX,INBOX,2
  2026-10-19T11:29:30+02:00,move_to_spam,INBOX,INBOX,2
  ```

- Moved mail can be dragged back from Spam in Gmail.
- Read state from the `mark_read` rows can be undone:

  ```bash
  uv run python cleanup_mail.py --undo-read-audit cleanup-audit.csv --dry-run
  uv run python cleanup_mail.py --undo-read-audit cleanup-audit.csv
  ```

  This restores the unread flag for exactly the UIDs recorded as `mark_read`.
  Messages recorded as `move_to_spam` are not moved back automatically.

Before a large irreversible operation, a Google Takeout export of Mail is a
reasonable belt-and-braces backup.

## Why the spam step may find nothing

Gmail classifies spam on its servers and puts it in the Spam folder. It does
**not** publish a per-message spam score in IMAP headers. The script therefore
only moves messages that some other server or filter explicitly labelled with
`X-Spam-Flag: YES` or `X-Spam-Status: Yes`. On a normal Gmail inbox that set is
usually empty.

If your goal is to reduce spam in the inbox, the practical options are:

1. Let Gmail handle it and rely on the Spam folder (nothing for this script to
   do), or
2. Mark messages as spam in Gmail itself so its filter learns, or
3. Use `--spam-from`, `--spam-subject`, or `--spam-contains` to move mail that
   matches your own sender/subject blocklist (see
   [Move mail that matches your own rules](#move-mail-that-matches-your-own-rules)).

The unread cleanup is unaffected and works as expected.

## Tests

The suite uses an in-memory IMAP server. The default large test builds a
70,000-message mailbox: 20,000 spam, 100 decoys labelled `X-Spam-*: NO`, and
60,000 unread messages. Run everything with:

```bash
uv run python -m unittest discover -s tests -t . -v
```

or:

```bash
./run_tests.sh
```

The simulation scales with `CLEANUP_TEST_SIZE` (spam is 2/7 of the mailbox,
unread is 6/7):

```bash
CLEANUP_TEST_SIZE=500000 ./run_tests.sh
CLEANUP_TEST_SIZE=1000000 ./run_tests.sh
```

Measured on an 8-core Linux machine (in-memory server, so real IMAP runs are
network-bound rather than CPU-bound):

| Mailbox size | Cleanup time | Peak RSS |
| ---: | ---: | ---: |
| 70,000 | 1.3 s | 94 MB |
| 500,000 | 9.8 s | 379 MB |
| 1,000,000 | 21.3 s | 733 MB |

The large test asserts that:

- 2/7 of messages move as spam and the remaining unread messages are marked
  read;
- no message is lost or duplicated (header multiset before/after);
- every remaining inbox message is read;
- the audit log has exactly the expected number of rows
  (`2 × spam + (unread - spam)`);
- IMAP commands never exceed the 200-UID / 100-fetch batch limits;
- MOVE is used when the server advertises it.

Smaller tests cover dry runs, aborts, `--limit`, `--no-spam`, `--no-unread`,
custom spam rules, idempotency, the COPY+EXPUNGE fallback, audit-log undo,
folder parsing, UTF-7 folder names, spam-header false positives, and both
authentication paths.

## Limitations

- IMAP only. Gmail's web-only categories (Promotions, Social, etc.) are seen as
  labels/folders, not as a separate classification.
- The script does not delete anything. It only changes flags and moves messages.
- On a mailbox with millions of messages, the initial search can be slow; use
  `--folder` or `--limit`.
- Gmail may take a few seconds to update unread counts in the web UI.
