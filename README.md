# QuantVexa Signals

Standalone signal system covering **all signal types** we built, in **2 files**.

## Files
- `server.js` — Node.js HTTP server (no dependencies, pure `http` module)
- `index.html` — Self-contained frontend (HTML + CSS + JS in one file)

## Signal Types
1. **NORMAL signals** — short-duration (1-3 min) BUY/SELL, available to all levels
2. **FUTURE signals** — long-duration (5-15 min) premium early-access, Level 1+ only

## Per-Level Daily Allowance (admin-configurable)
| Level | Label          | Normal/day | Future/day |
|-------|----------------|------------|------------|
| 0     | Normal Users   | 5          | 0          |
| 1     | Pro            | 10         | 2          |
| 2     | Pro+           | 20         | 5          |
| 3     | Elite          | 50         | 15         |

## Timezone
**All timestamps are UTC** (candle timezone). No browser timezone detection anywhere.

## Run
```bash
node server.js
# Open http://localhost:8080
```

## API
| Method | Path | Description |
|--------|------|-------------|
| GET    | `/health` | Health check |
| GET    | `/api/admin/signal-config` | Full per-level config |
| PUT    | `/api/admin/signal-config/:level` | Update one level `{normalSignals, futureSignals}` |
| POST   | `/api/admin/users/:tg_id/promote` | Set user level (0-3) |
| GET    | `/api/me/allowance?tg_id=ID` | User's own allowance + used today |
| GET    | `/api/signals?tg_id=ID&limit=N` | Signals visible to that user |
| GET    | `/api/signals/:id` | Single signal |

## How it works
- Server generates a new **normal** signal every ~20s and a new **future** signal every ~60s
- Signals expire after their duration and get a random `win`/`loss` result (demo)
- Frontend polls `/api/signals` every 3s and re-renders countdowns every 1s
- Admin can change any level's allowance via the top bar — changes apply instantly
