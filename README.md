# email-rag

[![test](https://github.com/sunsun-88/email-rag/actions/workflows/test.yml/badge.svg)](https://github.com/sunsun-88/email-rag/actions/workflows/test.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

Pull all emails from your mailbox → remove duplicate content → export into individual Markdown files with metadata,
making it easy to import into a RAG (Retrieval-Augmented Generation) knowledge base. **Everything runs only on your local machine; emails never leave your network.**

## What problem does it solve

Duplicate emails in a mailbox are usually forwards, replies, and accumulated multiple versions of the same matter—feeding them directly into RAG will drown retrieval results, with the same question recalled five times. This tool's processing pipeline is:

1. **Exact duplicates**: `Message-ID` + normalized body hash; a match is merged into one record
2. **Near duplicates**: custom SimHash-64 + Hamming distance; similarity ≥ 0.95 is automatically merged
3. **Gray zone goes to a human or local LLM**: "similar but uncertain" pairs between 0.75 and 0.95 enter the review queue
   - By default, you confirm them one by one;
   - After configuring a local OpenAI-compatible model (vLLM / Ollama / DeepSeek, etc.), use `--llm` for batch adjudication,
     with automatic direct-decision thresholds (≥0.90 directly judged duplicate, <0.80 directly judged non-duplicate) to control call volume,
     with support for incremental persistence, resumable runs, automatic failure degradation, and circuit breaking throughout.

Compared with a generic "IMAP downloader + Markdown exporter", this project additionally focuses on the **deduplication adjudication pipeline**: three-level deduplication, gray-zone review, LLM batch adjudication, and resumable runs.

## Features

- Supports standard **IMAP** mailboxes using "account + authorization code" login (common providers such as QQ, 163, Gmail)
- **Does not parse attachments**; only processes the body (original emails are fully stored in local SQLite; exported Markdown does not contain attachments)
- Emails exist only on **your local machine** (SQLite + Markdown) and are not uploaded to any server
- The same email (and "almost identical" emails produced by forwarding/replying) keeps only one representative and is not exported repeatedly
- Incremental fetching: watermark recorded in local SQLite; daily use processes only new emails
- Exported Markdown has YAML frontmatter (subject, sender, recipients, date, original folder, dedup status, content hash), making it easy to filter by metadata before ingesting

---

## Table of Contents

- [What problem does it solve](#它解决什么问题)
- [Features](#特性)
- [Quick Start (first time, follow along)](#快速开始第一次用照着做)
- [Configure Email](#配置邮箱)
- [What the Five Commands Do](#五个命令分别做什么)
- [Review Queue: Let You Confirm "Whether It Counts as Duplicate"](#复核队列让你确认算不算重复)
- [Daily Incremental Use](#日常增量使用)
- [What Exported Markdown Looks Like](#导出的-markdown-长什么样)
- [Troubleshooting](#常见问题排查)
- [Advanced: LLM Automatic Review](#进阶llm-自动复核)
- [Advanced: Run the Program Automatically Every Day](#进阶让程序每天自动跑)
- [Developer Information](#开发者信息)
- [Contributing Guide](#贡献指南)
- [License and Disclaimer](#许可与免责)

---

## Quick Start (first time, follow along)

> All operations are performed in PowerShell, with instructions for each step. It is recommended to run commands in the project root directory (the folder where
> `README.md` and `config.yaml` are visible).
>
> The relative paths of data files (`data/emails.db`, `data/review_queue.json`) and the export directory (`output/`)
> are resolved based on the directory containing `config.yaml`—even if commands are run from another directory, data will
> not "get lost" elsewhere. `data/emails.db` is the **`data\emails.db`** under the project directory
> (for example, `D:\my-project\email-rag\data\emails.db`).

### Step 1: Install the Python Environment

Requires Python 3.10 or later. Run in the project directory:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

After activation, `(.venv)` appears at the beginning of the command line, and you can then use the `email-rag` command directly.
**Note: activation is only valid for the current window**—a newly opened PowerShell window must be activated again.

> The activation command on macOS / Linux is `source .venv/bin/activate`; all other steps are exactly the same.
> In addition, all commands can also be written as `python -m email_rag.cli <command>` (same effect), suitable
> when the virtual environment is not activated.

### Step 2: Configure Email Connection Information

Copy a config file and edit it (see [Configure Email](#配置邮箱) for details):

```powershell
Copy-Item .env.example .env
```

Open `.env` with Notepad and fill in your email server, account, and **authorization code** (for QQ/163, this is not the login password):

```ini
IMAP_HOST=imap.qq.com
IMAP_PORT=993
IMAP_USER=yourQQnumber@qq.com
IMAP_PASSWORD=yourAuthorizationCode
```

### Step 3: Do a Small Test Run First (recommended to avoid pulling the entire mailbox right away)

Edit `config.yaml` and change `folders` to fetch only one folder (for example, INBOX):

```yaml
fetch:
  folders: INBOX        # First fetch only INBOX as a test
```

### Step 4: Fetch → Parse → Deduplicate → Export

```powershell
email-rag fetch    # Pull emails from the mailbox to the local machine (project directory\data\emails.db)
email-rag parse    # Parse raw emails into structured records
email-rag dedup    # Deduplicate; uncertain ones will be listed in the review queue
email-rag export   # Export as Markdown to the output/ folder
```

If you see `exported: N`, it succeeded, and the Markdown files are in `output/`. See
[command descriptions](#五个命令分别做什么) for detailed explanations.

### Step 5: After Confirming Everything Is Fine, Change to Fetch All Emails

Change `folders` in `config.yaml` back to `all`, then rerun the four commands in step 4.
`fetch` automatically skips emails already fetched and only pulls new ones.

---

## Configure Email

### What information is needed

You need four pieces of information: IMAP server address, port, account, and authorization code. Get the authorization code from the mailbox settings
after "enabling IMAP service" (QQ/163/Gmail cannot use the login password directly).

| Mailbox | IMAP Server | Port | How to Get Authorization Code | Compatibility |
|---|---|---|---|---|
| QQ Mail | `imap.qq.com` | 993 | Settings → Account → Enable IMAP/SMTP → Generate authorization code | Expected to work |
| 163 Mail | `imap.163.com` | 993 | Settings → POP3/SMTP/IMAP → Enable IMAP → Generate authorization code | Expected to work |
| Gmail | `imap.gmail.com` | 993 | Enable two-step verification → Generate "app-specific password" | Expected to work |
| Corporate email | Ask IT | Usually 993 | Ask IT for the IMAP address and authorization code/app password | Depends on whether IT enables IMAP |
| Outlook/Hotmail (personal) | `outlook.office365.com` | 993 | Microsoft has disabled "direct IMAP login with password" | ⚠️ Requires OAuth; this tool does not currently support it |

> Fun fact: port `993` is the encrypted (SSL) port for IMAP and generally does not need changing. Most mailboxes use it.
>
> **Important note**: All automated tests for this tool run against a simulated IMAP service (not a real mailbox).
> Mailboxes marked "Expected to work" above **have not yet been integration-tested with your real account**—their server addresses, ports,
> and authorization code methods are each provider's public standard practice, but for first use, please follow
> [Step 3: Do a Small Test Run First](#第-3-步先小范围试跑推荐避免一上来拉全邮箱) to fetch one folder
> and verify. If a mailbox reports an error, please share the error message so we can adapt specifically.
>
> **About Outlook personal**: Microsoft has disabled basic authentication (account + password) for IMAP login; the OAuth
> authorization flow is required. This tool currently uses direct connection with account + authorization code, so **personal Outlook/Hotmail is not currently available**;
> corporate Exchange can be used if IT enables basic authentication.

### How to Fill in `.env`

`.env` is your secret file (stores the authorization code), protected by `.gitignore` and will not be accidentally uploaded online.

```ini
# ===== Required: Email connection =====
IMAP_HOST=imap.qq.com        # IMAP server address
IMAP_PORT=993                # Port, usually fixed at 993
IMAP_USER=12345678@qq.com    # Full email address
IMAP_PASSWORD=abcdefghijklmno # Authorization code, not login password

# ===== Optional: LLM automatic review (see "Advanced: LLM Automatic Review") =====
# General way: fill these three items; leave LLM_PROVIDER empty
LLM_API_KEY=
LLM_MODEL=
LLM_BASE_URL=
# Lazy way: fill deepseek or openai and use only the default endpoint/model (in this case only fill LLM_API_KEY)
LLM_PROVIDER=
```

### How to Fill in `config.yaml`

`config.yaml` controls "what to fetch, how fast to fetch, and what counts as duplicate". The default configuration generally does not need changes.

```yaml
fetch:
  folders: all               # all=all folders except excluded ones; you can also write "INBOX, 已发送"
  exclude: [Junk, Trash, 垃圾邮件, 已删除]   # These folders are not fetched
  rate_limit_per_sec: 2      # Maximum emails fetched per second; too small is slow, too large may trigger mailbox rate limiting
  reconnect_max: 5           # Maximum reconnection attempts after disconnection

dedup:
  auto_merge_threshold: 0.95 # Similarity ≥0.95 automatically merged (keep representative)
  review_threshold: 0.75     # 0.75~0.95 enters review queue for your confirmation; <0.75 treated as different emails

export:
  output_dir: output         # Markdown output directory
```

> The two `dedup` thresholds do not need to be changed in a hurry. First run with defaults, see whether the contents in the review queue match your expectations, then fine-tune as needed.

---

## What the Five Commands Do

All commands are run in the **project directory**. They can be **run repeatedly and interrupted at any time**—
each step only processes "not yet processed" items; if interrupted, run again to continue.

### 1. `email-rag fetch` — Pull Emails to the Local Machine

Connect to the mailbox and store raw email content in `data/emails.db`.

- First run: fetch all historical emails (tens of thousands may take a long time, depending on `rate_limit_per_sec`)
- Subsequent runs: only fetch new emails, automatically skipping already fetched ones (determined by IMAP email number)
- Network dropped/window closed midway: just run again; it continues from the breakpoint

During execution, the terminal continuously prints progress, for example:

```
Connecting to imap.qq.com:993 ...
Will process the following folders: INBOX, 已发送
Starting to fetch INBOX: 5231 to fetch
INBOX: progress 200/5231 (cumulative new writes: 200)
INBOX: rate-limit wait 100 seconds...
INBOX: progress 400/5231 (cumulative new writes: 400)
```

Seeing "rate-limit wait" is **normal**—it is intentionally slowed according to `rate_limit_per_sec`
in `config.yaml` to prevent the mailbox server from rate-limiting or banning the account. It is also normal that the file size does not change
while each batch of 200 emails is downloading (the whole batch is written to the database at once after downloading).

> Note: IMAP's `BODY.PEEK[]` transfers the complete email content (including attachment bytes). Attachments do not
> enter the export, but raw bytes are temporarily stored in `data/emails.db` (for re-parsing), so the database
> will be much larger than body text alone—mailboxes with many attachments averaging a few hundred KB per email are normal; for mailboxes with hundreds of thousands of emails, reserve
> enough disk space. The optimization to "transfer only text parts" is planned; the current version prioritizes functional correctness.

### 2. `email-rag parse` — Parse Raw Emails into Structured Records

Extract sender, recipients, subject, time, body, etc. from raw emails in the database.

- Attachments are skipped (not parsed, not exported; raw bytes remain in the local database)
- Only parses emails not yet parsed; repeated runs do not process duplicates

### 3. `email-rag dedup` — Deduplicate

Judges each email in two rounds:

- **Exact duplicates** (the same email delivered repeatedly, or same Message-ID): automatically merged, keeping the one with the most complete content
- **Nearly identical** (slight differences caused by forwarding or replying):
  - Similarity ≥ 0.95 → automatically merged
  - Similarity 0.75~0.95 → **put into the review queue** for your confirmation (see next section)

Deduplication is also incremental: new emails are compared with previously processed emails, and duplicates across multiple runs can also be caught.

### 4. `email-rag export` — Export Markdown

Write retained emails after deduplication as Markdown files into `output/`.

- Emails from repeated runs are not exported repeatedly
- Re-exporting the same email is an "overwrite update" and will not produce multiple copies

### 5. `email-rag report` — View Statistics

Check at any time how many emails have been processed and their status:

```powershell
email-rag report
```

Output meaning:

- `raw`: total number of emails pulled from the mailbox
- `emails`: number of parsed records
- `by_status`:
  - `unique`—distinct emails retained (will be exported)
  - `kept`—representative retained in a duplicate group (will be exported)
  - `duplicate`—judged duplicate, not exported
  - `review`—waiting for your confirmation (see next section)

---

## Review Queue: Let You Confirm "Whether It Counts as Duplicate"

During deduplication, email pairs with "similarity 0.75~0.95 that the program is unsure about" are written to a file:

```
data/review_queue.json
```

It looks like this (simplified):

```json
{
  "pairs": [
    {
      "a": 12, "b": 15,
      "similarity": 0.86,
      "subject": "Meeting notice",
      "preview_a": "The meeting has been rescheduled to 10 AM tomorrow. Please attend on time.",
      "preview_b": "The meeting has been rescheduled to 10:30 AM tomorrow. Please attend on time."
    }
  ]
}
```

- `a` / `b`: IDs of the two emails in the database
- `preview_a` / `preview_b`: a short excerpt from the beginning of each email body, for easy visual comparison
- `similarity`: similarity calculated by the program (0~1)

### How to Confirm and Apply

Open `data/review_queue.json` with Notepad / VS Code, and for each pair add a
`verdict` field; there are only two possible values:

- Fill in `"duplicate"`: considered duplicate and will be merged (keeping the one with more complete content)
- Fill in `"unique"`: considered different emails, both retained

For example, change it to:

```json
{
  "a": 12, "b": 15,
  "similarity": 0.86,
  "subject": "Meeting notice",
  "preview_a": "The meeting has been rescheduled to 10 AM tomorrow. Please attend on time.",
  "preview_b": "The meeting has been rescheduled to 10:30 AM tomorrow. Please attend on time.",
  "verdict": "duplicate"
}
```

After filling in all pairs, run:

```powershell
email-rag dedup data/review_queue.json
```

The program will apply the results according to your judgment; just export once more:

```powershell
email-rag export
```

> Don't worry about filling it wrong: **before importing**, you can change it freely and repeatedly. It only takes effect at
> the moment you run `dedup <queue file>`; once imported, the status of these two emails is fixed, and changing
> `verdict` afterward will not change the result. If you import incorrectly, the reliable remedy is to run
> `email-rag dedup --full` (together with `email-rag export --prune`) to re-judge from scratch.
>
> Also: the next time `dedup` generates a new queue, **adjudicated** entries are automatically cleared, and **unadjudicated**
> ones are retained and will not be lost.

---

## Daily Incremental Use

The mailbox receives new emails every day. For each subsequent batch of new emails, just run in this order:

```powershell
email-rag fetch     # 1. Only pull new emails (fast)
email-rag parse     # 2. Only parse new ones
email-rag dedup     # 3. Deduplicate (including comparison with historical emails)
email-rag export    # 4. Export new/updated Markdown
```

If step 3 produces a review queue, process it (see previous section) before exporting; if no queue is produced, go directly to step 4.

> **A small reminder**: it is recommended to process the review queue after each round of `dedup` and then do the next round of `fetch`.
> If the review queue has not been processed, newly arrived similar emails will temporarily not be compared with "pending confirmation" emails and may be
> exported as new emails. Once you finish processing the previous queue, this problem will not occur.

### Full Re-deduplication (reconciliation/fallback)

Occasionally you may want to "re-deduplicate all emails", for example after changing dedup thresholds:

```powershell
email-rag dedup --full        # Reset all email statuses and deduplicate from scratch
email-rag export --prune      # Re-export and clean up invalid old files
```

- `--full` re-judges every email and may select a different "representative"
- Because `export` by default only adds and does not delete, old files will remain after changing the representative, so use `--prune`
- `--prune` deletes old `.md` files in `output/` that were not exported this time (returns the number deleted)

---

## What Exported Markdown Looks Like

One `.md` file per email, organized by "mailbox folder / year-month":

```
output/
└── inbox/                    # Mailbox folder (lowercased)
    └── 2024-03/              # Year-month
        ├── 20240316-quarterly-financial-report-a1b2c3d4.md
        └── 20240317-team-building-activity-e5f6g7h8.md
```

File content = a section of "metadata" (YAML format) + email body:

```markdown
---
subject: "Quarterly Financial Report"
from: "Zhang San <zhang@example.com>"
to: ["lisi@example.com"]
cc: []
date: "2024-03-16T09:00:00+08:00"
folder: "INBOX"
message_id: "<abc123@example.com>"
labels: []
dedup: "kept"
content_hash: "a1b2c3d4"
---

The financial report has been uploaded. Please check it.
```

- The part between the leading `---` is metadata, making it convenient for RAG systems to filter retrieval by sender/time/subject
- `dedup`: `kept` means it is the representative of a duplicate group; `unique` means it was originally unique
- Below is the plain body, which can be read directly by knowledge base tools

---

## Troubleshooting

### Q1: Running the command reports "The term 'email-rag' is not recognized as the name of a cmdlet..."

**The virtual environment is not activated** (activation is only valid for the current window; a new window must be activated again). Run in the project directory:

```powershell
.\.venv\Scripts\Activate.ps1
```

After success, `(.venv)` appears at the beginning of the prompt. If a red error occurs during activation (PowerShell execution
policy restriction), first run the following line and then activate again (it only affects the current window; closing the window automatically restores it and
does not affect system settings):

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

If you don't want to activate, you can also run directly with the full path: `.\.venv\Scripts\email-rag.exe fetch`
(exactly the same effect).

### Q2: `fetch` reports "login failed / authentication error"

Most likely these reasons; check in order:

1. **Used the login password instead of the authorization code**. QQ/163/Gmail all require an authorization code/app-specific password.
2. **IMAP service was not enabled in the mailbox settings**. For QQ, go to "Settings → Account" and enable it manually.
3. **Server address/port filled in incorrectly**. Check against the table above.
4. Try removing spaces around `IMAP_PASSWORD` (copy-paste easily includes spaces).

### Q3: `fetch` runs very slowly / feels rate-limited

This is normal; the first fetch of tens of thousands of emails is inherently slow, and the terminal will show "progress" and "rate-limit wait" prompts. To speed up,
you can increase `rate_limit_per_sec` in `config.yaml` (for example, to `5`), but setting it too high may
trigger the mailbox's frequency limit or even a temporary ban; **conservative settings are recommended**.

### Q4: `fetch` reports a folder does not exist / skips folders

Folder names differ across mailboxes. By default, trash-like folders listed in `exclude` are skipped. If you
want to fetch a specific folder, first check what it is actually called in the mailbox web version, then change `folders` in `config.yaml`.

### Q5: The `preview` in the review queue is hard to understand / emails are too long

`preview` is the first 300 characters of the body, enough to judge "whether they are emails about the same matter". If two
previews look like forwards/replies about the same matter, usually choose `duplicate`.

### Q6: Exported Markdown has fewer/more emails

Run `email-rag report` to see statistics:

- A large number of `review` → there are still unconfirmed ones; go process the review queue
- A large number of `duplicate` → the program considers them duplicates; if you think a certain email should not be merged, adjust
  `auto_merge_threshold` lower (for example, `0.9`) and rerun `dedup --full`

> To check how a certain email was processed, use a one-line Python command (no extra tools needed):
>
> ```powershell
> python -c "import sqlite3; c=sqlite3.connect('data/emails.db'); print(c.execute('SELECT id,subject,dedup_status,duplicate_of FROM emails WHERE id=12').fetchone())"
> ```

### Q7: Will my email content be uploaded online?

No. By default all processing is completed on your local machine, and emails exist only in `data/emails.db` and
`output/`. **Only when you actively configure and use `dedup --llm`** will a small amount of text snippets from the review queue
(the first 300 characters of each email, not the complete email) be sent to your configured LLM service for judgment.
It is recommended to use a locally deployed LLM for this work.

### Q8: Where are the attachments?

This tool **does not parse or export attachments**; only email bodies are exported. Note: during fetching, raw emails (including attachment bytes) are fully stored in local `data/emails.db` (see the explanation in the `fetch` section). The database size mainly comes from this—this data always remains only on your local machine.

---

## Advanced: LLM Automatic Review

If there are too many emails in your review queue and you want AI to help judge "whether it counts as duplicate", fill in three items in `.env`:

```ini
LLM_API_KEY=yourAPIKey
LLM_MODEL=deepseek-chat
LLM_BASE_URL=https://api.deepseek.com
```

Then:

```powershell
email-rag dedup --llm
```

It will batch-send the "truly ambiguous" email pairs in the review queue to the LLM for judgment and automatically fill in `verdict`
(including unadjudicated entries accumulated from previous rounds—even if there are no new emails, rerunning `dedup --llm` will also adjudicate the queue).

To control the number of calls and token consumption (thresholds are in the `dedup` section of `config.yaml`):

- **High-confidence ends are directly decided by rules without calling the LLM**: similarity ≥0.90 is directly judged duplicate, <0.80 directly judged non-duplicate;
  only the [0.80, 0.90) gray zone is sent to the LLM;
- **Batch calls**: the gray zone is combined into one request according to `llm_batch_size` (default 20 pairs);
- **Output cap**: output limit per call = `llm_output_tokens_per_pair` (default 200) × batch size,
  `temperature=0`, returns only JSON. The default 200/pair is based on actual testing (some models tend to analyze first and then output;
  setting it too small will truncate and cause parsing failures) and can be adjusted according to your model's behavior;
- **Minimize sending**: only send a 300-character snippet from the beginning of each email body; do not send complete emails or metadata.

After judgment, export as usual.

A few notes:

- **Any OpenAI-compatible service can be used** (DeepSeek, local vLLM, Ollama, etc.)—as long as it provides
  the `/chat/completions` interface, fill the corresponding key, model name, and service address into the three items above;
  `LLM_PROVIDER` **does not need to be filled**.
- `LLM_PROVIDER` is useful only in one case: when you are too lazy to fill in the address and model name, write `deepseek` or
  `openai` to use the built-in defaults (in this case, only fill in `LLM_API_KEY`).
- If `--llm` is provided but the key/address is not configured properly, the program will clearly prompt and skip automatic adjudication; it will not fail silently.

> This feature calls external APIs and incurs a small cost; cost-effective services such as DeepSeek are recommended.
> If you mind, use manual review; the functionality is exactly the same, just a few more clicks.

---

## Advanced: Run the Program Automatically Every Day

If you want to fetch, deduplicate, and export on a schedule every day, you can use Windows Task Scheduler:

1. Open Task Scheduler → Create Basic Task
2. For the trigger, choose "Daily" and select a time (for example, 3 AM)
3. For the action, choose "Start a program":
   - **Program**: fill in the full path to `email-rag.exe`, for example
     `C:\path\to\email-rag\.venv\Scripts\email-rag.exe`
     (note that the full path must be written; Task Scheduler does not read the PATH in your terminal)
   - **Arguments**: fill in `fetch` (only pull new emails); to run the full workflow, see the batch script below
   - **Start in**: fill in the full path of the project directory, for example `C:\path\to\email-rag`

If you want to run the full workflow, first create an `auto.bat` in the project directory (replace the path with your own):

```bat
@echo off
cd /d C:\path\to\email-rag
call .venv\Scripts\activate
email-rag fetch
email-rag parse
email-rag dedup
email-rag export
```

Then let Task Scheduler call this `auto.bat`:
- Program: fill in `cmd.exe`; Arguments: fill in `/c C:\path\to\email-rag\auto.bat`
- Start in: also fill in the project directory path

The tasks execute local scripts and likewise will not upload emails.

---

## Developer Information

To view the code structure, run tests, and modify code:

```bash
# Run all tests
pytest -q

# View test coverage
pytest --cov=email_rag --cov-report=term
```

Project structure:

```
src/email_rag/
├── cli.py        # Command-line entry (fetch/parse/dedup/export/report)
├── config.py     # Read .env and config.yaml
├── fetcher.py    # Connect to IMAP to fetch emails (rate limiting, reconnection, resumable transfer)
├── parser.py     # Parse email body (skip attachments, handle Chinese encoding)
├── store.py      # SQLite database (store emails and dedup status)
├── simhash.py    # Similarity algorithm (judge whether two emails are similar)
├── dedup.py      # Main dedup logic + review queue
├── llm.py        # LLM automatic review (optional)
└── exporter.py   # Export Markdown
```

See `docs/superpowers/specs/2026-10-10-email-rag-design.md` for the design document.

---

## Contributing Guide

Issues and PRs are welcome. Before making changes, please read `docs/superpowers/specs/`; design constraints are recorded there.

- Run all tests before submitting: `pytest -q`
- For new features, please add tests as well; PRs that modify `src/email_rag/` will be verified by CI on Python 3.10 / 3.11 / 3.12 separately
- Do not submit any real email content, credentials, or `.env`; CI's leak-guard will directly block them on PRs
- Try to make each change do only one thing; use Chinese body text in commit messages

## License and Disclaimer

This project is licensed under [Apache-2.0](LICENSE).

**Disclaimer**: This tool only works under your existing mailbox account and does not provide any email service. The email content imported into the knowledge base, along with its copyright and confidentiality obligations, remains with you and the original author. Please confirm on your own whether it complies with your organization's compliance requirements.
