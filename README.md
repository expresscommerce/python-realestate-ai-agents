# PropertyBot 🏡

**Your friendly AI real estate assistant.** Chat with PropertyBot to find homes to buy or rent anywhere in the United States — it pulls live listings from Redfin, helps you compare options, and provides direct links to book visits on the property's website.

---

## Quick Start

### Pull the Repository

```bash
git clone <your-repo-url>
cd real_estate_ai_agent
```

### Prerequisites

- **Python 3.10 or newer**
- **[uv](https://github.com/astral-sh/uv)** — a fast Python package manager. Install it with one command:
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

### Setup & Run

1. **Install dependencies**
   ```bash
   uv sync
   ```

2. **Install Playwright browsers** (required for scraping)
   ```bash
   uv run playwright install
   ```
   If you're on Linux and see a warning about missing system libraries:
   ```bash
   sudo $(which playwright) install-deps
   ```

3. **Add your API key** — Create a `.env` file in the project root:

   **Option 1 — DeepInfra (free tier available):**
   ```env
   DEEPINFRA_API_KEY=your_deepinfra_api_key_here
   DEEPINFRA_MODEL=deepinfra/moonshotai/Kimi-K2.6
   ```
   > Get a key at [deepinfra.com](https://deepinfra.com).

   **Option 2 — OpenAI:**
   ```env
   OPENAI_API_KEY=your_openai_api_key_here
   DEEPINFRA_MODEL=openai/gpt-4o
   ```

   **Option 3 — Groq (fast & free):**
   ```env
   GROQ_API_KEY=your_groq_api_key_here
   DEEPINFRA_MODEL=groq/llama-3.3-70b-versatile
   ```

   **Option 4 — Anthropic Claude:**
   ```env
   ANTHROPIC_API_KEY=your_anthropic_api_key_here
   DEEPINFRA_MODEL=anthropic/claude-3-5-sonnet
   ```

   > See [LiteLLM docs](https://docs.litellm.ai/docs/providers) for all supported providers.

4. **Start PropertyBot**
   ```bash
   uv run uvicorn backend.real_estate_ai_agent.main:app --reload --host 0.0.0.0 --port 8000
   ```

   Open **http://localhost:8000** in your browser and start chatting!

---

## Features

- 💬 Chat naturally — just say "I want a 3-bed house in Austin under $700k"
- 🔍 Pulls **real, live listings** from Redfin (no fake data)
- 📅 Provides direct links to book visits on the property's website
- 🏘️ Works in any US city (Austin, NYC, Miami, SF, Seattle, and more)
- 🧠 Remembers your conversation — no repeating yourself

---

## Architecture

```
User → FastAPI (/api/chat) → Chat Pipeline (chat.py)
                              ↓
                         LiteLLM (LLM with tools)
                              ↓
                         Tool Dispatch (tools.py)
                              ↓
        ┌───────────────────┼───────────────────┐
        ↓                   ↓                   ↓
   Redfin Scraper    Web Scraping       Address Lookup
   (Playwright)      (Playwright)        (Redfin)
                              ↓
                        Return Results + Links
                              ↓
                        User books on Redfin
```

### Key Components

| Component | File | Purpose |
|-----------|------|---------|
| **FastAPI App** | `backend/real_estate_ai_agent/main.py` | API endpoints, static files, CORS |
| **Chat Pipeline** | `backend/real_estate_ai_agent/chat.py` | LLM loop, tool calling, history management |
| **Tools** | `backend/real_estate_ai_agent/tools/` | Redfin search, address lookup, web scraping (split into modules) |
| **System Prompt** | `backend/real_estate_ai_agent/prompts.py` | LLM instructions, requirement gathering rules |
| **Session Store** | `backend/real_estate_ai_agent/session.py` | Redis with in-memory fallback |
| **Config** | `backend/real_estate_ai_agent/config.py` | Environment variables |
| **Browser** | `backend/real_estate_ai_agent/integrations/ghost_browser.py` | Playwright headless browser wrapper |
| **Scraper** | `backend/real_estate_ai_agent/integrations/scraper_client.py` | HTTP client with fallback |
| **Frontend** | `frontend/static/index.html` | Chat UI, served at `/` |

---

## Tools

PropertyBot exposes 6 tools to the LLM via LiteLLM function-calling:

| Tool | Purpose | Parameters | Status |
|------|---------|------------|--------|
| `search_listings` | Scrape Redfin for property listings | `city`, `state`, `listing_type` (sale/rent), `property_type`, `bedrooms_min/max`, `price_max`, `limit`, `offset` | ✅ Operational |
| `web_search_property` | Look up a specific address on Redfin | `address`, `city`, `state` | ✅ Operational |
| `schedule_visit` | Book a property visit | `property_id`, `client_name`, `client_phone`, `date`, `time` | ❌ Not operational (users book via Redfin link) |
| `check_availability` | Show visit slots for next 7 days | `property_id` | ❌ Not operational (users book via Redfin link) |
| `scrape_web_page` | Scrape any URL | `url`, `render_js` | ✅ Operational |
| `extract_web_data` | Extract structured data from URL | `url`, `schema_json`, `prompt` | ✅ Operational |

### Tool Implementation Pattern

All tools are defined in `tools.py` with:

1. **Function signature** with type hints
2. **Docstring** describing behavior
3. **Return value** as a string (LLM-readable)
4. **Tool schema** in `TOOL_DEFS` for LiteLLM

Example:
```python
def search_listings(
    city: str,
    state: str,
    listing_type: str = "",
    property_type: str = "",
    bedrooms_min: int | None = None,
    bedrooms_max: int | None = None,
    price_max: int | None = None,
    limit: int = 5,
    offset: int = 0,
) -> str:
    """Search property listings by scraping Redfin."""
    # Implementation...
    return formatted_results
```

---

## Chat Pipeline (`chat.py`)

### Flow

1. **Build messages** — System prompt + recent history (last 20 turns)
2. **Call LLM with tools** — LiteLLM handles tool invocation
3. **Loop** — If LLM calls a tool:
   - Execute tool via `run_tool()`
   - Append tool result to history
   - Call LLM again with result
4. **Return** — Final assistant response

### Key Functions

| Function | Purpose |
|----------|---------|
| `process_chat(history)` | Main entry point, mutates history in place |
| `_build_messages(history)` | Construct LiteLLM message array |
| `_call_llm(messages, use_tools)` | LiteLLM completion with tool definitions |
| `_recent_user_content(history)` | Concatenate last 4 user messages for context |

---

## System Prompt (`prompts.py`)

The system prompt enforces **strict requirement gathering** before searching:

### 5 Required Fields

1. **Buy or rent** — `"buy"` or `"rent"`
2. **Property type** — `"house"`, `"apartment/condo"`, `"multi-family"`, `"land/plot"`
3. **City/Location** — City name + state code
4. **Budget** — Maximum price or range
5. **Bedrooms** — Exact count, min, max, or range

### Rules

- **NEVER search** until all 5 fields are present
- **Ask for missing fields** in a single friendly message
- **NEVER assume or default** missing values
- **Search IMMEDIATELY** once all 5 are present
- **Map cities to states** via hardcoded list (Austin→TX, NYC→NY, etc.)
- **Map property types** to Redfin codes (house→SFR, apartment→CONDO, etc.)

### Bedroom Parameter Logic

| User says | Pass to tool |
|----------|--------------|
| "3 bedrooms" | `bedrooms_min=3, bedrooms_max=3` |
| "at least 3" | `bedrooms_min=3` only |
| "up to 3" | `bedrooms_max=3` only |
| "2 to 4" | `bedrooms_min=2, bedrooms_max=4` |

---

## Session Management (`session.py`)

### Redis Storage

- Key pattern: `sess:{session_id}`
- Value: JSON-serialized list of `{"role": "user|assistant", "content": "..."}`
- TTL: 24 hours (`SESSION_TTL = 60 * 60 * 24`)

### Fallback

If Redis is unavailable or `REDIS_URL` is empty, uses in-memory dict `_local`. Data is lost on restart.

### Functions

| Function | Purpose |
|----------|---------|
| `get_history(session_id)` | Retrieve chat history |
| `save_history(session_id, history)` | Persist chat history |
| `clear_history(session_id)` | Delete session |

---

## Redfin Scraping (`tools.py`)

### URL Construction

Redfin URLs are built with `_redfin_search_url()`:

```
https://www.redfin.com/city/{state_id}/{city_slug}/{rental_segment}/filter/{filters}
```

- **State mapping**: TX→8903, WA→30818, FL→3245, NY→6271, etc.
- **City slug**: Lowercase, hyphens instead of spaces
- **Filters**: `min-beds=X,max-beds=Y,max-price=Z,property-type=TYPE`

### Scraping Flow

1. Build Redfin URL from parameters
2. Launch Playwright headless browser via `GhostBrowser`
3. Navigate to URL, wait for content
4. Extract listing cards (price, address, beds/baths, sqft, URL)
5. Filter by city/state (Redfin sometimes returns nearby areas)
6. Format as bullet-point display text

### Pagination

- Use `offset` parameter: `offset=0` for first 5, `offset=5` for next 5, etc.
- `limit` controls batch size (default 5, max 10)

---

## Browser Integration (`integrations/ghost_browser.py`)

### GhostBrowser Class

Wraps Playwright for headless scraping:

```python
browser = GhostBrowser(headless=True)
browser.navigate(url)
html = browser.get_html()
browser.close()
```

### Features

- Custom user agent rotation
- Configurable timeout
- Automatic cleanup on context exit

---

## Environment Variables

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `DEEPINFRA_API_KEY` | Yes | — | LLM provider API key |
| `DEEPINFRA_MODEL` | No | `deepinfra/meta-llama/Meta-Llama-3.1-70B-Instruct` | LiteLLM model identifier |
| `REDIS_URL` | No | _(empty → in-memory)_ | Redis connection string |
| `CORS_ORIGINS` | No | `*` | Comma-separated allowed origins |
| `GHOST_BROWSER_ENABLED` | No | `1` | Enable Playwright fallback (`0` to disable) |
| `REALESTATE_API_KEY` | No | — | Reserved for future API integration |

---

## Optional: Redis for Persistent Sessions

By default, your chat history lives in memory — it disappears when you restart the server. Want it to survive restarts?

1. **Start Redis** (easiest with Docker):
   ```bash
   docker run -d -p 6379:6379 --name propertybot-redis redis
   ```

2. Add this to your `.env`:
   ```env
   REDIS_URL=redis://localhost:6379/0
   ```

That's it — sessions now persist for 24 hours.

---

## API Endpoints

| Method | Endpoint | What it does |
|--------|----------|--------------|
| `GET` | `/api/health` | Health check |
| `POST` | `/api/session` | Create a new chat session |
| `GET` | `/api/session/{id}` | Get a session's history |
| `DELETE` | `/api/session/{id}` | Clear a session |
| `POST` | `/api/chat` | Send a chat message |

**Interactive API docs:** http://localhost:8000/docs

### Example chat request

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "I want to buy in Austin"}'
```

```json
{
  "session_id": "abc-123",
  "response": "Great! A few quick questions to find the best options..."
}
```

---

## Frontend (`static/index.html`)

### Tech Stack

- Vanilla HTML/CSS/JS (no frameworks)
- Tailwind-inspired custom CSS
- LocalStorage for session persistence
- Click delegation for new-tab links

### Key Features

- Real-time chat UI
- Quick-action buttons
- Auto-growing textarea
- Typing indicator
- Markdown formatting (bold, italic, lists, links)
- All links open in new tab (via click delegation + `target="_blank"`)

### API Integration

```js
const res = await fetch('/api/chat', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ session_id: sessionId, message: text }),
});
const data = await res.json();
sessionId = data.session_id;
localStorage.setItem('pb_sid', sessionId);
```

---

## Development Workflow

### Adding a New Tool

1. Implement function in `tools.py` with type hints and docstring
2. Add schema to `TOOL_DEFS` list
3. Add to `_DISPATCH` dictionary
4. Document in `prompts.py` tool list
5. Test via chat UI or API

### Modifying System Prompt

- Edit `SYSTEM_PROMPT` in `prompts.py`
- Restart server (uvicorn `--reload` picks up changes)
- Clear localStorage/session to test fresh behavior

### Debugging

```bash
# Check Redis connection
redis-cli ping

# View Redis sessions
redis-cli KEYS "sess:*"
redis-cli GET "sess:<session_id>"

# Test LLM directly
uv run python -c "from src.real_estate_ai_agent.config import DEEPINFRA_API_KEY, DEEPINFRA_MODEL; print(f'Key: {bool(DEEPINFRA_API_KEY)}, Model: {DEEPINFRA_MODEL}')"

# Run with verbose logging
LOGLEVEL=DEBUG uv run serve
```

---

## Deployment Notes

- **Port**: Default 8000 (configurable in `main.py`)
- **CORS**: Wide open by default — restrict via `CORS_ORIGINS` for production
- **Redis**: Recommended for production (sessions persist across restarts)
- **Playwright**: Requires system browser dependencies — run `playwright install-deps` on host
- **Static files**: Served from `/` via `FileResponse` with cache-busting headers

---

## Common Issues

| Issue | Fix |
|-------|-----|
| Port 8000 in use | Kill process or change port in `main.py` |
| Playwright browser warnings | Run `sudo $(which playwright) install-deps` (Linux) |
| No listings returned | Check `DEEPINFRA_API_KEY` and Playwright install |
| Redis connection refused | Start Redis or remove `REDIS_URL` from `.env` (fallback to in-memory) |
| Links don't open in new tab | Hard refresh browser (Ctrl+Shift+R) — HTML was cached |

---

## File Reference

```
real_estate_ai_agent/
├── backend/
│   └── real_estate_ai_agent/
│       ├── main.py              (119 lines)  FastAPI app, endpoints, serve()
│       ├── chat.py              (206 lines)  LLM loop, tool calling
│       ├── prompts.py           (102 lines)  System prompt
│       ├── session.py           (69 lines)   Redis + in-memory sessions
│       ├── config.py            (12 lines)   Env vars
│       ├── tools/
│       │   ├── __init__.py      (TOOL_DEFS, run_tool dispatch)
│       │   ├── redfin.py        (Redfin URL builders, mappings)
│       │   ├── listings.py      (search_listings, web_search_property)
│       │   ├── visits.py        (schedule_visit, check_availability)
│       │   └── web.py           (scrape_web_page, extract_web_data)
│       └── integrations/
│           ├── ghost_browser.py (843 lines)  Playwright wrapper
│           └── scraper_client.py (121 lines) HTTP client
└── frontend/
    └── static/
        └── index.html       (Frontend chat UI)
```

---

## Future Enhancements

- [ ] Make visit scheduling tools operational (currently users book directly via Redfin link)
- [ ] Multi-city comparison
- [ ] Price trend analysis
- [ ] Image-based property matching
- [ ] SMS/email notifications
- [ ] User accounts and saved searches

---

## License

Proprietary — TekGlide.
