# Campaign Studio — Final Work and Progress Report

Updated: **2026-10-19** · Consolidated implementation, setup, validation and deployment handoff.

This is the single maintained Markdown document for this application. It combines the earlier October 17–19 progress reports, operating instructions and legacy-worker notes. Earlier missing-token and stale-server issues described in those reports were subsequently resolved; the current deployment blocker is listed below.

## Current status

| Area | Status |
| --- | --- |
| Local application | Implemented and running; `GET /api/status` was rechecked during consolidation |
| Frontend/backend integration | Active Campaign Studio screens use backend data and saved workspace settings |
| Campaign workflow | Creation, drafts, audience, scheduling, delivery controls, inbox and metrics implemented |
| Conversions, Analytics and Reports | Saved data, automatic report snapshots and backend CSV exports implemented |
| Personal Telegram delivery | Earlier handoff records one successful authorized private test and user confirmation |
| Automated validation | Six active check scripts passed; Telegram calls in these checks were simulated |
| Browser verification | Visual layout and real browser clicks remain unverified because no browser was connected |
| GitHub | Published to `main`; application/merge milestones are `9621757` and `aa0aba1` |
| Vercel | Project linked; **no production deployment completed** |
| Deployment blocker | Neon Free database provisioning requires the account owner's terms acceptance, followed by cloud persistence/worker adaptation |

Repository: [Telegram-Campaign-Tool](https://github.com/itz-rajshekhar18/Telegram-Campaign-Tool).

## Run locally

Prerequisites: Python, the packages in `requirements.txt`, and Node.js for the JavaScript verification scripts.

```powershell
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -m app.main
```

Copy `.env.example` only when `.env` does not already exist. Keep existing credentials and database files when updating this checkout. Alternatively, use `install.bat` for dependency setup.

Set private values in the root `.env`:

```env
APP_ENV=development
TELEGRAM_BOT_TOKEN=<your-BotFather-token>
CHAT_ID=<your-positive-private-Telegram-user-ID>
```

Open http://127.0.0.1:8000. The local development shortcut accepts loopback requests without Telegram Mini App data; production requires signed Telegram identity and restricts management to the configured owner. Private recipients must open the bot chat and press **Start** before the bot can message them.

Run one app process with `python -m app.main`. It starts without hot reload and owns one delivery worker. Restart after code or `.env` changes. `HOST`, `PORT` and `DATABASE_URL` can override runtime configuration. The normal database is `data/campaigns.db`; static files and `.env` resolve from the project directory.

## Completed functionality

### Campaign workspace and drafts

- Replaced the temporary personal-test console with Campaign Studio: campaign collection, search/status filters, brief editor, campaign tabs, audience, inbox, conversions, analytics, reports and connection settings. The implementation includes desktop sidebar and narrow-screen navigation; appearance has not been visually verified.
- Campaigns persist objective, audience/size, product, value proposition, proof, offer, CTA, information bank, primary metric, target and deadline. Added category, budget, currency, landing page, language, tone, tags and internal notes. URL/budget validation and additive migrations preserve existing records. Currency changes are rejected once daily metrics exist.
- SMART checks and AIDA section assessments describe brief/draft completeness. Empty goal and audience counts require user entry; the app does not seed campaigns or performance results.
- Draft generation renders **one to five editable templates** saved in workspace settings, using the actual campaign brief. Template labels, wording and sections are configurable. Supported placeholders are supplied by the backend; unsafe attribute/format expressions are rejected. Empty or oversized rendered sections produce validation errors rather than canned output.
- Draft sections can be edited, saved, previewed and explicitly sent as personal tests. Brief changes retain existing drafts. STOP instructions remain enforced. Templates are deterministic; they are not LLM output or automatic translation.

### Audience, subscriptions and inbox

- Campaign invitation links enroll private bot chats through `/start <campaign token>`. Manual additions require explicit consent and a reachable private chat. Contact stages, notes, opt-out controls and CSV export are connected to saved data.
- Polling stores incoming text and its update cursor while the server runs. `STOP`, `/stop` and `/unsubscribe` deactivate that chat's campaign subscriptions, and pending campaign sends recheck subscription state. Opted-out contacts must resubscribe themselves.
- Plain-text replies are attributed to the most recent successful campaign delivery to that contact. The inbox shows the latest 300 messages. Replies require an explicit owner action and are saved.
- External webhooks are reported as polling conflicts; the app does not delete another integration's webhook. Campaign delivery pauses when incoming updates cannot be synchronized.

### Persistent campaign delivery

- Launch review displays the exact saved message, recipient count and optional schedule before confirmation. Launch snapshots the message and subscribed audience into SQLite. One unfinished send per campaign is allowed.
- The worker supports scheduled/overdue sends, per-contact outcomes, pause/resume/cancel, consent rechecks and Telegram flood-control delays. Keep the server running for schedules, subscriptions and incoming replies.
- Send pacing is configurable through `SEND_INTERVAL_SECONDS`, initially 1.2 seconds plus network time. Telegram's explicit `retry_after` delays a rejected send. Ordinary failures are not automatically retried.
- A message already in flight can finish after pause/cancel. Interrupted or uncertain delivery is marked **unknown** and is not automatically replayed. Check Telegram before initiating another send for affected contacts.
- Contacts, daily metrics and campaign delivery history export through authenticated backend CSV endpoints.

### Bot credentials and direct messaging

- **Connection** (`/#settings`) verifies and saves `TELEGRAM_BOT_TOKEN` and owner `CHAT_ID` in the private server `.env`, preserving unrelated keys/comments. Leave the token blank to retain it; restart to apply changes. No raw token is returned by settings reads or stored in browser storage.
- The owner ID controls workspace management. Destination IDs are entered separately. MTProto `API_ID`/`API_HASH` are legacy credentials and do not replace a Bot API token.
- Direct sending accepts one or multiple unique destinations: positive private IDs, negative group IDs or public `@channel` usernames. Separate them by lines, spaces or commas. The backend-configured limit is initially 20 and can be lowered with `DIRECT_SEND_MAX_RECIPIENTS`.
- An optional temporary token sends through another bot without changing the workspace bot. A blank token uses the active workspace bot, not settings awaiting restart. Existing campaigns prevent replacing the workspace bot with a different bot ID because subscription/polling state belongs to the original bot.
- Permission confirmation and message/recipient review precede sending. The bot must already have access and posting permissions. Duplicate destinations are sent once. Per-chat results distinguish sent, failed, unknown and skipped.
- Direct batches stop remaining recipients after an uncertain transport result, invalid token or Telegram rate limit. They do not retry automatically. Keep the page open until results arrive; after interruption, inspect history and Telegram before resending.
- `DirectSendRecord` persists batch text, bot ID and per-chat outcomes. Each attempted recipient is recorded as unknown before the network request. Credentials are never saved in history. After restart, unattempted pending recipients become skipped and unknown results remain unknown.
- Connection displays the latest 50 saved direct batches. Direct batches run during their request; they are separate from campaign jobs, reports, subscriptions and STOP handling. Use Audience/Delivery for subscriber campaigns and scheduling.

### Conversions and Analytics

- The conversion ledger stores purchases, signups, bookings, qualified leads and other outcomes, with campaign/contact/variant attribution, status, amount/currency, source, occurrence date, reference and notes.
- References are unique within a campaign. Edit records to confirmed, pending, refunded or cancelled. Campaign/date/status/currency/search filters, summaries, pagination, editing and complete filtered CSV export are implemented. Summaries cover all matching records, not just the visible page.
- Analytics uses saved delivery, daily metric and ledger data for date/campaign/currency filters, date shortcuts, daily SVG trends, reported funnel, budget/spend, ROAS, delivery health, campaign comparison, conversion sources and current contact stages. Daily and comparison CSV generation now runs on the backend.
- **Ledger conversions and manually entered aggregate conversions remain separate and are never added together.** Confirmed Telegram sends also remain separate from manually entered delivered counts.
- Financial totals never combine currencies and do not apply exchange rates. ROAS uses confirmed ledger value divided by entered spend in the selected currency; pending/refunded/cancelled records do not count as confirmed value. Refund value is reported separately.
- Live sends/replies use UTC dates; entered metrics and ledger events use their saved reporting dates. Contact stages and planned budget show current values independently of the date filter. Date ranges are limited to 366 days.
- There is no automatic payment ingestion, purchase webhook, click pixel, read-receipt tracker or external conversion importer.

### Automatic Reports and exports

- **Reports** (`/#reports`) reads the same backend calculations and ledger records as Analytics and Conversions. It supports all campaigns or one campaign, date range and financial currency.
- Report content includes live delivery, entered analytics totals, confirmed conversion value/counts, per-currency ledger summaries, daily activity, campaign comparisons and the latest ten conversion records.
- Opening, refreshing or exporting automatically saves a `SavedReport` snapshot in SQLite. Matching campaign/date/currency scopes reuse the same record; changed data updates it, while unchanged data retains its save timestamp.
- Reports refreshes while open at the workspace interval, initially 30 seconds, without replacing the filter form. Failed refreshes retain the previous displayed report. Snapshot saving is access-triggered; it is not a background archive job.
- No manual copy or separate Report Save action is required. Daily aggregate metrics remain editable in an individual campaign's Results tab, and saved conversion changes appear on the next report refresh.
- Export **Summary CSV**, **Daily analytics CSV**, **Campaign comparison CSV** or **Conversions CSV**. Exports use applied campaign/date filters and current saved source data. The selected currency controls financial summaries; conversion rows retain their own currencies. Conversion export includes every matching record, beyond the ten-row preview. CSV text cells are escaped against spreadsheet formula execution.

### Backend configuration and real data

- **Connection → Workspace defaults & reporting rules** stores category, currency, language, tone, conversion source, recommendation thresholds, default report date range/refresh interval and editable draft templates in `WorkspaceSettings`.
- Frontend forms load backend metadata and defaults from `/api/studio/workspace`. Currency/category/tone inputs are not restricted to fixed frontend lists. Supported workflow states, metric types, conversion types/statuses, contact stages and direct-send limits are provided by the backend.
- New settings affect new forms and future draft generation without rewriting existing campaigns/messages. Recommendation alerts and comparison floors use saved workspace settings. Backend output supplies quality-score denominators.
- `/api/status` reads owner-scoped database campaign counts and actual token-configured state rather than returning fixed zero counts.
- Telegram API base URL, send interval and direct-recipient limit are server configuration. Unused legacy rate-profile presets were removed. Interface labels, chart styling, protocol/validation constraints, supported workflow semantics and simulated test fixtures remain code constants; they are not fabricated application results.

## Storage, security and source layout

- SQLite schema initialization and additive migrations retain existing campaign data. Remote SQLite hardening was preserved: WAL mode, normal synchronization, connection timeout and busy timeout.
- Authentication validates signed Telegram Mini App `initData`, freshness and duplicate fields. Management is restricted to the configured owner; local development and the owner's Mini App share this single workspace. Production does not accept the loopback development shortcut.
- Credential validation errors omit rejected input. Telegram failures do not echo token-bearing URLs or provider descriptions containing secrets. `.env`, local databases/backups, session files, logs, Vercel metadata and the nested native Telegram repository are excluded from publication/deployment as appropriate.
- Private backups made during development include `data/campaigns-before-insights-20261017-235815.db`, `data/campaigns-before-reports-20261018.db` and `data/campaigns-before-workspace-20261018.db`. They are local and ignored by Git.

| Files | Responsibility |
| --- | --- |
| `app/main.py` | Campaign/draft/metric/personal-test APIs, status and app lifecycle |
| `app/studio.py` | Audience, subscriptions, inbox, campaign delivery worker and exports |
| `app/connection.py` | Bot settings, direct sends, durable results and restart recovery |
| `app/workspace.py` | Workspace defaults, editable templates and frontend metadata |
| `app/insights.py` | Conversion ledger, Analytics and conversion export |
| `app/reports.py` | Auto-saved report snapshots and backend report CSVs |
| `app/campaign_tools.py` | Template rendering, SMART/AIDA checks and recommendations |
| `app/models.py`, `app/database.py` | Persistence, schema initialization and SQLite hardening |
| `app/telegram_auth.py`, `app/config.py` | Identity validation and environment configuration |
| `static/miniapp.html`, `static/studio.css`, `static/studio.js` | Active application shell and campaign UI |
| `static/insights.js`, `static/reports.js`, `static/connection.js`, `static/workspace.js` | Analytics, reporting, settings and history UI |
| `scripts/check_*.py`, `scripts/check_*_ui.cjs` | Active isolated API/workflow and simulated frontend checks |

## Validation completed

All six active check scripts passed during implementation and again after the remote merge:

```powershell
python scripts/check_backend.py
python scripts/check_connection.py
python scripts/check_reports.py
node scripts/check_workspace_ui.cjs
node scripts/check_connection_ui.cjs
node scripts/check_reports_ui.cjs
```

- Full workflow: saved defaults/custom three-template generation, campaigns, consent rejection, audience persistence, launch/pause/resume, actual worker code with simulated send responses, inbox reply, metrics and conversion defaults, Analytics/Report equality, CSVs, direct-history persistence/restart recovery, real status counts and authorization.
- Reporting/connection edges: snapshot reuse/update, conversion edits, currency isolation, date/owner rejection, empty states, duplicate references/destinations, partial failures, rate limits, unknown-send handling, concurrency, credential preservation/redaction, full CSV record counts, parser checks and formula escaping.
- Simulated frontend behavior: metadata/default hydration, template editing, configurable report timer, review before sending, temporary-token clearing, duplicate-submission guard, applied export filters, retaining prior reports on refresh failure and direct-history rendering.
- Python compilation, JavaScript syntax checks and Git whitespace checks passed, with line-ending notices only. Source passed a staged credential-pattern scan before publication.
- Live local root/assets and workspace, overview, campaign, connection, Analytics, Conversions, Reports, direct-history and CSV endpoints returned successful responses during their respective checks. Local `/api/status` was rechecked during this consolidation.
- Earlier project notes record a successful, explicitly authorized personal Telegram test and user confirmation. No campaign broadcast was sent to establish bulk-delivery proof. Automated checks used simulated Telegram responses; they do not prove real delivery for every recipient or bot.
- **Visual browser QA remains pending.** No connected browser was available for desktop/mobile screenshots, clicks or a real browser network-flow check.

## GitHub publication and legacy history

- Repository: `https://github.com/itz-rajshekhar18/Telegram-Campaign-Tool`; branch: `main`.
- `9621757` committed the backend-driven Campaign Studio, settings and automatic Reports.
- `aa0aba1` merged remote revision `24382ec` without force-pushing. Publication was verified against `origin/main` before documentation consolidation.
- The current Bot API architecture supersedes the old MTProto user-account worker. Remote tests remain at `legacy/mtproto/campaign_worker_tests.py`; they target retired `app/telegram_manager.py` and are historical reference, not the active suite. To execute them with their original runtime, restore revision `24382ec3d8b55181202fb55e2876dbb35febc213` in a separate checkout. Legacy files are excluded from deployment.
- This report replaces the dated progress files and the duplicate legacy README. Dependency-owned documentation and instruction files are outside that cleanup scope. The documentation cleanup is committed after the application milestones above; `git log -1` identifies its commit.

## Vercel status and remaining deployment work

The project is linked as **`irnahuras-projects/telegram-campaign-tool`**, but **there is no completed production deployment or verified live Vercel application URL**.

1. GitHub push access and Vercel authentication were available. Vercel's GitHub app could not connect to this collaborator-owned repository; CLI deployment remains possible. Automatic Git-triggered deployment has not been configured.
2. The current application still uses a single-process SQLite queue. Durable hosted storage and a dedicated worker are required before publishing a functional Vercel deployment. Vercel's documented Daemons feature is in public beta and uses ephemeral storage; merely uploading local SQLite or serving a disconnected static page would not complete deployment.
3. Neon **Free** plan `free_v3` provisioning was attempted for production in region `iad1`, with built-in auth disabled. The CLI returned `integration_terms_acceptance_required`. **No database resource was created, no paid plan was selected, and local data/secrets were not uploaded.**
4. Required account-owner action: [accept Neon terms in Vercel](https://vercel.com/irnahuras-projects/~/integrations/accept-terms/neon?source=cli). Terms were not accepted on the user's behalf. The user had not confirmed completion when this report was consolidated.
5. After that confirmation, retry the same free provisioning operation once and inspect its actual result. Then adapt persistence, worker lifecycle and credential configuration for the hosted environment, verify the complete flow, deploy through the linked Vercel project and check the live APIs/exports and worker. **These cloud adaptations have not yet been implemented.**
6. After a verified HTTPS deployment, configure the Mini App URL in BotFather and open it from the configured owner's Telegram account. No BotFather URL/menu change has been performed.

Official references: [Vercel Services](https://vercel.com/docs/services), [Vercel Daemons](https://vercel.com/docs/daemons), [Telegram Bot API](https://core.telegram.org/bots/api) and [Mini App identity validation](https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app).

## Known limits and next verification

- Keep one local server/worker running; distributed multi-worker deployment is unsupported by the current implementation. Always inspect unknown delivery before considering another send.
- Conversion capture, clicks/read receipts, purchase/payment ingestion, automatic translation and autonomous AI replies are not configured. Optional legacy AI/voice helpers and unserved legacy templates are outside the active workflow.
- Report snapshots refresh when accessed or while Reports is open, not continuously while the app is closed. Direct-send history and campaign analytics deliberately describe different workflows.
- Review the actual desktop/mobile UI when browser access is available, then run a small explicitly authorized campaign trial to verify receive/reply/STOP behavior before wider use.
- Complete the Neon account step and cloud adaptation before calling the Vercel deployment finished.
