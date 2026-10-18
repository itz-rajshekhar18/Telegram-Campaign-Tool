# Campaign Studio

A Telegram campaign workspace built with FastAPI, SQLite, and a responsive Mini App interface.

## Run locally

Install dependencies with `install.bat`, or `python -m pip install -r requirements.txt`. Save these settings in the private `.env` at the project root:

```env
APP_ENV=development
TELEGRAM_BOT_TOKEN=<BotFather bot token>
CHAT_ID=<your positive numeric private Telegram chat ID>
```

Then run `python -m app.main` and open http://127.0.0.1:8000. Local development access is available from loopback addresses. The owner must press **Start** in the bot chat before using personal test sends. MTProto `API_ID` and `API_HASH` are optional legacy credentials and do not replace a bot token.

Restart the app after changing `.env` or code. The launcher uses one server process without hot reload, with one delivery worker. Development binds to `127.0.0.1`; `HOST` and `PORT` can override server settings. The database lives at `data/campaigns.db`; `DATABASE_URL` can override it. Keep `.env`, the database, and Telegram session files private and back up the database before moving the app.

## Campaign workflow

1. **Create a campaign.** Set an objective, audience, numeric goal, deadline, product details, offer, CTA, and information bank. SMART checks describe brief completeness.
2. **Generate message drafts.** The app renders the editable draft templates saved in workspace settings using the campaign brief. These are deterministic templates, not LLM output; their number, labels, sections and wording come from saved settings. Edit and save each section, preview it, and optionally send yourself a test. Editing the brief preserves existing drafts.
3. **Build the audience.** Share the campaign subscription link with a clear invitation to receive its updates. Pressing Start subscribes that person to the campaign. Alternatively, add a reachable private chat ID after confirming explicit consent. Contact stages and notes are editable; opted-out contacts must resubscribe themselves.
4. **Review and launch.** Select a saved variant and optionally a local date/time. Review the exact message and recipient count, then confirm. The app snapshots the message and audience into a persistent delivery queue. One unfinished send per campaign is allowed.
5. **Monitor delivery.** Inspect per-contact results and pause, resume, or cancel pending messages. A message already in flight may still finish. Keep the server running for schedules; overdue queued messages resume when the server starts. Sending is paced at approximately one message per 1.2 seconds plus network time, and Telegram flood-control delays are honored.
6. **Manage replies.** The app polls Telegram while running, saves incoming text, and supports explicit manual replies. Reply attribution uses the most recent successful campaign send to that contact. The latest 300 inbox messages are shown. STOP, /stop, and /unsubscribe remove that chat from every campaign; future pending sends are skipped.
7. **Record results.** Daily outcomes are saved per variant and date. Re-saving replaces those figures. Comparison uses the selected primary metric. Clicks, conversions, signups, and read receipts are not automatically tracked. Confirmed Telegram sends remain separate from manually recorded delivered counts. Export contacts, metrics, and delivery history as CSV.

The Connection page shows bot status and keeps the personal test sender available. No messages are sent merely by opening the workspace. Subscriptions and STOP updates are processed while the server is running; **Sync Telegram** requests an immediate refresh.

### Configure your bot and send directly

Open **Connection** (`/#settings`) to enter `TELEGRAM_BOT_TOKEN` and the owner's private `CHAT_ID`. **Verify & save settings** verifies the token with Telegram and updates only these two keys in the private server `.env`. Leave the token blank to keep it. Restart with `python -m app.main` to apply saved changes. Tokens are never returned by the settings API or saved in browser storage. The workspace owner ID controls management access; destination IDs are entered separately below.

**Send to one or multiple chats** accepts up to the backend-configured recipient limit (20 by default) as unique destination IDs separated by lines, commas or spaces. Use positive private IDs, negative group IDs, or public `@channel` usernames. A temporary token lets you send through another bot without changing workspace settings. Leave it blank to use the currently active workspace bot, which may differ from settings awaiting restart. Review the exact message and recipients, then press **Send now**. The bot must already have access to each destination; private recipients must start it first.

Results show sent, failed, unknown or skipped for every chat. The sender paces requests and stops the remaining batch on an uncertain delivery, token rejection or rate limit; it does not retry automatically. Check Telegram before resending after interrupted requests. Keep the page open until results arrive. Per-chat results are persisted in direct-send history, including in-flight unknown results before network requests. These direct batches are separate from campaign jobs and do not appear in campaign reports, subscriptions or STOP processing. Pending direct recipients are marked skipped after a restart; uncertain deliveries are never replayed automatically. Use campaign Audience and Delivery for subscriber broadcasts and durable history. Existing campaigns prevent replacing the workspace bot with another bot because subscriptions and polling state belong to the original bot; use a separate workspace or the temporary-token sender instead.

Isolated checks: `python scripts/check_connection.py` and `node scripts/check_connection_ui.cjs`. They use simulated Telegram responses; the API check uses a temporary database and `.env`.

## Conversions and analytics

Campaign briefs also store category, budget, currency, landing page URL, language, tone, tags, and internal notes. Language and tone are review context for template drafts; no automatic translation is performed. Search includes tags and category. Campaign currency cannot change after daily results are recorded, preserving the meaning of historic spend.

**Conversions** is an individual outcome ledger. Record purchases, signups, bookings, qualified leads, or other outcomes with a status, amount, currency, source, occurrence date, reference, notes, and optional contact/variant attribution. References are unique within a campaign. Edit records to confirmed, pending, refunded, or cancelled. Filter by campaign, dates, status, currency, and name/reference; summaries cover all matching rows, including rows beyond the current page. Export the filtered ledger to CSV.

**Analytics** provides campaign/date/currency filters, 7/30/90-day shortcuts, a daily activity chart, a reported conversion funnel, budget/spend, ROAS, delivery health, campaign comparisons, conversion sources, and the current contact-stage pipeline. Daily trends and campaign comparisons can be exported.

Individual ledger outcomes and manually entered aggregate conversions are separate datasets and are never added together. The funnel uses aggregate metrics only; contacts are not deduplicated across events. ROAS is confirmed ledger value divided by entered spend in the selected currency. Pending, refunded, and cancelled ledger records are excluded from confirmed value; refund value is shown separately. No exchange rates are applied. Live sends/replies use UTC dates; manually entered metrics and ledger events use their saved dates. The audience pipeline and planned budget show their current state, independent of the date filter. Date ranges are limited to 366 days.

These pages record and summarize your saved data; no purchase webhook, payment integration, click pixel, or automatic conversion import is configured.

## Automatic Reports

Open **Reports** (`/#reports`) to generate a report from the same saved data used by Analytics and Conversions. Choose all campaigns or one campaign, a date range (up to 366 days), and a financial currency. Reports includes live delivery results, entered analytics totals, confirmed conversion counts and value, per-currency conversion summaries, daily activity, campaign comparisons, and the latest ten conversion records. Recorded conversions and entered aggregate conversions stay separate, preventing double-counting.

Reports requires no manual copy or Save action. Opening, refreshing or exporting generates and automatically saves a report snapshot in SQLite. While Reports is open, its displayed data refreshes at the configured workspace interval (30 seconds initially) without replacing the filter form. Reusing the same campaign/date/currency scope updates its saved snapshot; unchanged data retains the original save timestamp. Snapshots update when accessed, rather than running a background archive job. Daily metrics can still be entered in an individual campaign's **Results** tab; saved conversion entries appear in Reports automatically on the next refresh. Existing source records stay saved when the app closes.

Download **Summary CSV**, **Daily analytics CSV**, **Campaign comparison CSV**, or **Conversions CSV**. The Analytics page also downloads its daily and comparison CSVs from these backend routes. Exports use the applied campaign/date filters and current saved source data. The financial currency selects monetary totals; counts and the conversions export include all currencies with each record's own currency, matching the report's separate currency summaries. Conversion export includes every matching record, not just the ten-row preview. Other CSVs include report/date/currency metadata and protect text cells against spreadsheet formula execution.

Checks: `python scripts/check_reports.py` and `node scripts/check_reports_ui.cjs` validate isolated report persistence, consistency with Analytics, conversion edits, currency separation, date/owner filters, full CSV exports, frontend rendering and automatic refresh behavior without Telegram calls. Visual browser verification remains pending browser availability.

## Delivery guarantees and limits

- The local worker persists pending and completed delivery results. Interrupted sends are marked **unknown** and are never automatically replayed. Check Telegram before creating another send for those contacts.
- The queue does not retry ordinary failures. Telegram's explicit `retry_after` response delays a rejected send for a later attempt.
- Local polling cannot operate while an external Telegram webhook is configured. The app reports this conflict rather than deleting another integration's webhook. Queue sends pause if incoming updates cannot be synced, so pending STOP requests are not deliberately bypassed.
- Use a single app process/worker. This SQLite queue is designed for this local workspace, not multiple distributed workers.
- Replies are manual; legacy Ollama and voice modules are not wired into automatic replies. Campaign sends are private bot messages to subscribed contacts.

## Open inside Telegram

Host the app at a public **HTTPS** URL, set `APP_ENV=production`, keep the correct owner `CHAT_ID`, and configure the Mini App URL using **@BotFather**. Open the Mini App from the configured owner's Telegram account. Signed Telegram init data is verified server side; campaign management is restricted to that owner. Subscribers use the bot's campaign invitation links and do not need access to the management app.

Hosting and BotFather setup have not been performed by this local implementation. Run the app on a continuously running service for scheduled sends and polling. A server that stops or scales to zero will not process work until it runs again.

Telegram integration follows the official [Bot API](https://core.telegram.org/bots/api), including [getUpdates](https://core.telegram.org/bots/api#getupdates), [sendMessage](https://core.telegram.org/bots/api#sendmessage), and [Mini App authentication](https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app).

## Main files

**Connection → Workspace defaults & reporting rules** stores the default category, currency, language, tone, conversion source, recommendation thresholds, report date range/refresh interval, and editable message draft templates in SQLite. New forms load these values from `/api/studio/workspace`; saving settings affects new campaigns and future drafts without rewriting existing data. Currency/category/tone entry is no longer restricted to hardcoded frontend lists. Supported workflow states and metric types come from backend metadata. Initial defaults are explicit configuration, rather than seeded campaign results. Template placeholders insert the saved brief; no LLM or automatic translation is implied.

The active application uses API data for campaign lists/details, subscribers, delivery, inbox, conversions, analytics, reports, connection state, and direct-send history. `/api/status` now counts owner-scoped database records rather than returning fixed zero counts. Analytics CSV generation runs on the backend. Business rules that represent validation or workflow states, interface wording, chart styles, and test fixtures remain defined in code; they are not fabricated application data.

Deployment settings in `.env.example` include the Telegram API base URL, send pacing and direct-send recipient limit. The same pacing applies to campaign workers and direct sends; the direct recipient limit is supplied to the frontend by the backend. Private tokens remain in server `.env` or request memory, never in send history. Static assets resolve relative to the project directory.

Additional checks: `python scripts/check_backend.py` exercises settings → custom drafts → consent/audience → queued worker send → reply → metrics/conversion → analytics/report/CSV → direct history against a temporary database and simulated Telegram. `node scripts/check_workspace_ui.cjs` checks backend defaults, form options, workspace saving, report timer configuration, backend analytics exports, and history rendering.

- `app/main.py`: campaign, draft, metrics, and personal test APIs; app lifespan.
- `app/workspace.py` / `static/workspace.js`: owner workspace settings, editable templates, frontend metadata and saved direct history view.
- `app/connection.py`: owner credential settings and durable direct-send results; never persists bot tokens.
- `app/reports.py` / `static/reports.js`: auto-saved reports and server CSV exports.
- `app/studio.py`: audience, subscriptions, inbox, exports, and persistent delivery worker.
- `app/insights.py`: conversion ledger, filtered exports, and analytics aggregation.
- `app/models.py` / `app/database.py`: stored campaign data and additive SQLite migrations.
- `app/telegram_auth.py`: Telegram signature checks and loopback development access.
- `static/miniapp.html`, `studio.css`, `studio.js`: complete client workspace.
- `static/insights.js`: conversion entry, filters, charts, and analytics exports.

## Validation

Python compilation and JavaScript syntax checks passed. An isolated SQLite/API flow with simulated Telegram calls covered campaign creation/editing, draft generation, consent rejection, subscription links, STOP suppression, scheduling, pause/resume, worker delivery, inbox/reply, metrics upserts, reports, CSV export, and forged-auth rejection. No real broadcast was sent during implementation. The live local page/assets and studio APIs were checked, and the real bot connection returned success. Browser automation was unavailable, so visual and interactive browser verification remains pending.
