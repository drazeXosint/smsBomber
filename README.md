# smsBomber v2

A professional Telegram bot for OTP flood testing and SMS gateway abuse assessment. Built for security researchers to test rate limiting on their own platforms.

> **Disclaimer:** This tool is intended strictly for authorized security testing on systems you own or have explicit permission to test. Unauthorized use against third-party services is illegal.

---

## Features

### Core Testing
- Fire OTP requests to **43+ Indian APIs** simultaneously using a round-robin engine
- **Smart API distribution** — every API gets equal requests, no single API gets hammered
- **Confirmed OTP detection** — distinguishes real OTP sends (2xx + success keywords) from generic 2xx responses
- **Rate limit handling** — APIs that return 429 get a 60s cooldown then automatically re-enter the queue
- **Dead API detection** — APIs that error 3x in a row are marked dead for that session
- **Accurate timer** — hard deadline using `asyncio` event, stops at exactly the configured duration
- **Live dashboard** — updates every 2 seconds with progress bar, per-API breakdown, confirmed count
- **Proxy support** — validate and use SOCKS5/HTTP proxy pools, auto-falls back if no working proxies found

### API Management
- Add custom APIs via JSON config directly in the bot
- Edit, rename, delete any API (including base APIs by copying them to bot DB first)
- **Health Check** — fire all APIs simultaneously with a random number, see OK/Dead/Rate Limited/Error breakdown
- **Browse APIs** — Recently Added / All APIs / Dead APIs / Skipped APIs views
- **Skip/Enable APIs** — permanently exclude broken APIs from future tests without deleting them
- **Demo Test** — test a new API with a random valid Indian number before saving
- **Manual Test** — test with your own number before saving

### Admin Panel
- **User management** — paginated user list, tap any user to open profile
- **Per-user controls** — ban/unban, set custom daily limit, reset today's count, view test history
- **Global limit** — update daily limit for all users at once
- **Broadcast** — send a message to all registered users
- **Phone blacklist** — permanently block specific numbers from being used as test targets
- **Stats** — total users, active today, tests run, API counts
- **Proxy Manager** — upload proxy files, view pool, delete

### User Features
- Combined wizard — pick duration and workers on one screen, Continue appears when both are set
- **Repeat Test** — re-run last test config with one tap
- **My History** — view last 10 personal tests
- Daily test limits with midnight IST reset
- Protected number support

---

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Open main menu |
| `/menu` | Open main menu (same as /start) |
| `/admin` | Open admin panel (admin only) |

---

## Admin Panel Sections

### Users
- View all users as tappable buttons showing name and usage
- Tap any user → profile card with ID, status, tests today/limit, join date
- **Ban / Unban** — restrict or restore access
- **Set Limit** — set a custom daily test limit for that user
- **Reset Today** — reset their test count for the current day
- **History** — view their last 10 tests with phone, duration, OTP count

### Stats
- Total users, banned count, active today, tests run today
- Active API count, skipped API count

### API Manager
- **Add API** — paste JSON config, preview details, Demo Test with random number, Test with your number, then Save
- **List All** — paginated list of all APIs with [base]/[custom]/[edited] tags, tap any to open detail
- **Browse** — 4 views:
  - Recently Added — your custom APIs newest first
  - All APIs (A-Z) — alphabetical with type tags
  - Dead APIs — from last health check, with Skip/Delete options
  - Skipped APIs — currently disabled APIs with Enable buttons
- **Health Check** — fires all APIs simultaneously, shows OK/Dead/Rate Limited/Error counts, tap any category to browse results, Skip or Delete dead ones

### Proxy Manager
- Upload `.txt` proxy files with a label
- View all uploaded files with proxy count and date
- Delete proxy files
- All files merged into one pool, users toggle on/off per test

### Reset All
- Confirmation prompt → resets today's test count for every user

### Global Limit
- Set a single daily limit that applies to all users

### Broadcast
- Send any message (HTML supported) to all registered users
- Shows sent/failed count on completion

### Blacklist
- Add a 10-digit number with optional reason
- Blocked users see "That number is not available for testing"
- Remove numbers to unblock them

---

## API Config Format

APIs are stored as JSON objects with this structure:

```json
{
  "name": "SiteName",
  "method": "POST",
  "url": "https://api.example.com/send-otp",
  "headers": {
    "content-type": "application/json",
    "origin": "https://example.com"
  },
  "json": {
    "phone": "{phone}",
    "country_code": "91"
  }
}
```

### Placeholders

| Placeholder | Replaced with |
|-------------|---------------|
| `{phone}` | 10-digit target number |
| `{email}` | Target email |
| `{name}` | Random name |
| `{password}` | Random password |

### Supported body types

| Key | Content-Type |
|-----|-------------|
| `json` | application/json |
| `data` | application/x-www-form-urlencoded |
| `params` | Query string (GET requests) |

### Type coercion
If the original API sends phone as an integer (e.g. `{"phone": 919876543210}`), keep it as an integer in the config — the bot will send it as a number, not a string.

---

## Project Structure

```
smsBomber/
├── run.py                      ← Always use this to start the bot
├── .env                        ← BOT_TOKEN goes here
├── requirements.txt
├── apis.py                     ← Base API configs (43+ APIs)
├── helpers.py                  ← Placeholder replacement utilities
├── tester.py                   ← Original CLI tester (reference)
└── bot/
    ├── config.py               ← Bot settings (ADMIN_ID, limits, paths)
    ├── main.py                 ← Bot startup and router registration
    ├── utils.py                ← HTML formatting helpers
    ├── handlers/
    │   ├── start.py            ← Main menu, help
    │   ├── test_flow.py        ← Test wizard, dashboard, summary
    │   ├── dashboard.py        ← Settings screen
    │   ├── admin.py            ← Admin panel, users, blacklist, broadcast
    │   ├── admin_apis.py       ← API manager, health check, browse
    │   └── admin_proxy.py      ← Proxy manager
    ├── keyboards/
    │   └── menus.py            ← All inline keyboards
    ├── middleware/
    │   └── auth.py             ← User registration middleware
    └── services/
        ├── database.py         ← SQLite database layer
        ├── tester_runner.py    ← Round-robin engine, stats, proxy validation
        ├── api_manager.py      ← Merge base + custom APIs
        ├── api_loader.py       ← Load apis.py configs
        ├── proxy_manager.py    ← Proxy pool management
        └── scheduler.py        ← Midnight IST reset loop
```

---

## Installation

### Requirements
- Python 3.9+
- Windows / Linux / macOS

### Setup

```bash
# 1. Clone the repo
git clone https://github.com/yourusername/smsBomber.git
cd smsBomber

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create .env file
echo BOT_TOKEN=your_bot_token_here > .env

# 4. Set your Telegram user ID in bot/config.py
# ADMIN_ID: int = your_telegram_id

# 5. Run
python run.py
```

### Getting your Telegram ID
Message [@userinfobot](https://t.me/userinfobot) on Telegram — it will reply with your user ID.

### Getting a Bot Token
1. Open [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot`
3. Follow the prompts
4. Copy the token into `.env`

---

## Configuration

Edit `bot/config.py`:

```python
ADMIN_ID            = 123456789    # Your Telegram user ID
PROTECTED_NUMBER    = "9876543210" # Number that cannot be tested by anyone
DEFAULT_DAILY_LIMIT = 10           # Default tests per user per day
DASHBOARD_UPDATE_INTERVAL = 2.0   # Dashboard refresh rate in seconds
DEFAULT_WORKERS     = 4            # Default worker count
```

---

## Dependencies

```
aiogram==3.7.0          # Telegram bot framework
aiohttp==3.9.5          # Async HTTP client
aiohttp-socks==0.8.4    # SOCKS proxy support
python-dotenv==1.0.1    # .env file loading
```

---

## How the Engine Works

1. All APIs loaded into a **round-robin queue** at test start
2. Each worker pulls the next available API from the queue
3. Sends one request, records result, puts API back at the end of the queue
4. If API returns **429** → 60s cooldown, then re-enters queue
5. If API errors **3 times in a row** → marked dead for this session
6. A hard timer fires the stop event at exactly the configured duration
7. All in-flight requests are cancelled and TCP connections forcibly closed

**Confirmed OTP** = HTTP 2xx AND response body contains: `otp sent`, `sent successfully`, `"success":true`, `verification code sent`, or similar keywords.

**2xx Total** = all HTTP 2xx responses regardless of body content.

---

## Adding APIs

### From Chrome DevTools
1. Open the target site's login page
2. Open DevTools → Network tab → check **Preserve log**
3. Enter your phone number and click Send OTP
4. Find the request in the network tab (look for the one fired immediately on click, before the OTP input screen)
5. Right click → **Copy as cURL (Windows)** or **Copy as cURL (bash)**
6. Paste into the bot's Add API screen

### JSON Format Rules
- Phone as string: `"phone": "{phone}"`
- Phone with country code: `"phone": "91{phone}"`
- Phone as integer: `"phone": 91{phone}` (no quotes)
- Strip all cookies, user-agent, sec-* headers, captcha fields

### Sites to avoid
- Cookies contain `ak_bmsc` or `_abck` → Akamai protected, will 403
- Cookies contain `cf_clearance` → Cloudflare protected, will 403
- Response under 100ms with HTML → Edge block, skip
- Body contains `recaptcha_response` → reCAPTCHA required, skip
- Headers contain `x-fp-signature` + `x-fp-date` → HMAC signing, expires in minutes

---

## Database

SQLite database stored at `bot_data.db` in the project root (absolute path, never lost on restart).

### Tables
| Table | Contents |
|-------|----------|
| `users` | User ID, username, join date, ban status, daily limit, tests today |
| `testHistory` | Per-test records with phone, duration, workers, OTP hits, requests |
| `customApis` | Admin-added API configs as JSON |
| `proxyFiles` | Uploaded proxy file contents and metadata |
| `blacklistedPhones` | Blocked phone numbers with reason and date |
| `skippedApis` | API names excluded from future tests |

---

## Credits

Built by [@drazeforce](https://t.me/drazeforce)
