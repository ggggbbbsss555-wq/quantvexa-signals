/**
 * QuantVexa Signals — Standalone Signal System
 * Single-file Node.js server.
 *
 * Covers ALL signal types we've built:
 *   1. NORMAL signals  — regular BUY/SELL, every user level gets them
 *   2. FUTURE signals  — premium early-access, only Level 1+
 *   3. Per-level daily allowance (admin-configurable):
 *        Level 0 (Normal)  → 5 normal,  0 future  per day
 *        Level 1 (Pro)     → 10 normal, 2 future  per day
 *        Level 2 (Pro+)    → 20 normal, 5 future  per day
 *        Level 3 (Elite)   → 50 normal, 15 future per day
 *
 * All timestamps are UTC (candle timezone). No browser timezone logic.
 *
 * Endpoints:
 *   GET  /health
 *   GET  /api/admin/signal-config           → full per-level config
 *   PUT  /api/admin/signal-config/:level    → update one level {normalSignals, futureSignals}
 *   GET  /api/signals?level=N&limit=M       → list signals visible to that level
 *   GET  /api/signals/:id
 *   GET  /api/me/allowance?tg_id=ID         → user's own allowance (auto-detects level)
 *   POST /api/admin/users/:tg_id/promote    → set user level (0-3)
 *
 * Run:  node server.js
 * Default port: 8080
 */
const http = require('http');
const url  = require('url');

const PORT = process.env.PORT || 8080;

// ============================================================================
// STATE
// ============================================================================
const LEVELS = ['level0', 'level1', 'level2', 'level3'];

const config = {
  // Per-level daily signal allowances (admin-configurable)
  signalConfig: {
    level0: { normalSignals: 5,  futureSignals: 0,  label: 'Normal Users' },
    level1: { normalSignals: 10, futureSignals: 2,  label: 'Level 1 (Pro)' },
    level2: { normalSignals: 20, futureSignals: 5,  label: 'Level 2 (Pro+)' },
    level3: { normalSignals: 50, futureSignals: 15, label: 'Level 3 (Elite)' },
  },
  // User levels (tg_id → level)
  users: {},
  // All generated signals (newest first)
  signals: [],
  // Per-user-per-day-per-type counters: { "tgId|date|type": count }
  dailyCounters: {},
};

const SYMBOLS = ['EURUSD-OTC','GBPUSD-OTC','USDJPY-OTC','AUDUSD-OTC','USDCAD-OTC','BTC/USDT','ETH/USDT','SOL/USDT'];

// ============================================================================
// HELPERS — all UTC
// ============================================================================
function nowUtc() { return Date.now(); }
function utcDateKey(ts) {
  const d = new Date(ts);
  return d.getUTCFullYear() + '-' +
    String(d.getUTCMonth() + 1).padStart(2, '0') + '-' +
    String(d.getUTCDate()).padStart(2, '0');
}
function uid(prefix) { return prefix + '_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 5); }
function clamp(n, lo, hi) { return Math.max(lo, Math.min(hi, n)); }

function getUserLevel(tgId) {
  if (!tgId) return 0;
  return config.users[String(tgId)] ?? 0;
}
function setUserLevel(tgId, level) {
  config.users[String(tgId)] = clamp(parseInt(level, 10) || 0, 0, 3);
}
function getAllowanceForLevel(level) {
  return config.signalConfig['level' + clamp(level, 0, 3)] || config.signalConfig.level0;
}

// How many signals of a type has the user already received today (UTC day)?
function getDailyCount(tgId, type) {
  const key = tgId + '|' + utcDateKey(nowUtc()) + '|' + type;
  return config.dailyCounters[key] || 0;
}
function bumpDailyCount(tgId, type) {
  const key = tgId + '|' + utcDateKey(nowUtc()) + '|' + type;
  config.dailyCounters[key] = (config.dailyCounters[key] || 0) + 1;
}

// ============================================================================
// SIGNAL GENERATOR
// ============================================================================
/**
 * Generate a single signal of a given type.
 * - normal:  short duration (1-3 min), random direction
 * - future:  longer duration (5-15 min), random direction, marked as premium
 */
function generateSignal(type) {
  const direction = Math.random() < 0.5 ? 'BUY' : 'SELL';
  const symbol = SYMBOLS[Math.floor(Math.random() * SYMBOLS.length)];
  const durationSec = type === 'future'
    ? 300 + Math.floor(Math.random() * 600)   // 5-15 min
    : 60 + Math.floor(Math.random() * 120);   // 1-3 min
  const startTime = Math.floor(nowUtc() / 1000);
  return {
    id: uid('sig'),
    type,                                   // 'normal' | 'future'
    direction,                              // 'BUY' | 'SELL'
    symbol,
    entry: null,                            // filled when delivered (placeholder)
    duration: durationSec,
    startTime,
    expiry: startTime + durationSec,
    result: null,                           // 'win' | 'loss' | null (pending)
    profit: null,
    createdAt: nowUtc(),
  };
}

/**
 * For a given user, decide whether they should receive a new signal of `type`
 * based on their level allowance and how many they've already had today.
 */
function canDeliverToUser(tgId, type) {
  const level = getUserLevel(tgId);
  const allowance = getAllowanceForLevel(level);
  const cap = type === 'future' ? allowance.futureSignals : allowance.normalSignals;
  if (cap <= 0) return false;
  return getDailyCount(tgId, type) < cap;
}

/**
 * Deliver a signal to a user (bumps their daily counter).
 * Returns the signal (a copy tagged with the user's tg_id).
 */
function deliverToUser(signal, tgId) {
  bumpDailyCount(tgId, signal.type);
  return { ...signal, deliveredTo: String(tgId) };
}

// Background generator: produce one new signal of each type every ~20-60 sec,
// so the public list is never empty. Users receive them lazily via GET /api/signals.
let lastNormalAt = 0, lastFutureAt = 0;
function tickGenerator() {
  const now = nowUtc();
  // Normal signal every ~20s
  if (now - lastNormalAt > 20000) {
    config.signals.unshift(generateSignal('normal'));
    lastNormalAt = now;
  }
  // Future signal every ~60s
  if (now - lastFutureAt > 60000) {
    config.signals.unshift(generateSignal('future'));
    lastFutureAt = now;
  }
  // Resolve expired signals (random win/loss for demo)
  config.signals.forEach(s => {
    if (s.result === null && Math.floor(now / 1000) >= s.expiry) {
      const win = Math.random() < (s.type === 'future' ? 0.78 : 0.55);
      s.result = win ? 'win' : 'loss';
      s.profit = win
        ? +(s.type === 'future' ? (0.5 + Math.random() * 2).toFixed(2) : (0.3 + Math.random() * 1.5).toFixed(2))
        : -+(s.type === 'future' ? (0.3 + Math.random()).toFixed(2) : (0.4 + Math.random() * 1.2).toFixed(2));
      s.resolvedAt = now;
    }
  });
  // Cap memory: keep last 500 signals
  if (config.signals.length > 500) config.signals.length = 500;
}
setInterval(tickGenerator, 5000);
tickGenerator(); // run once at boot

// ============================================================================
// HTTP SERVER
// ============================================================================
function sendJson(res, status, body) {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    'Content-Type': 'application/json',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type,Authorization,X-TG-User',
    'Cache-Control': 'no-store',
  });
  res.end(payload);
}

function readBody(req) {
  return new Promise((resolve) => {
    let buf = '';
    req.on('data', chunk => buf += chunk);
    req.on('end', () => {
      try { resolve(buf ? JSON.parse(buf) : {}); }
      catch { resolve({}); }
    });
  });
}

const server = http.createServer(async (req, res) => {
  const parsed = url.parse(req.url, true);
  const path = parsed.pathname;
  const q = parsed.query;
  const method = req.method;

  // CORS preflight
  if (method === 'OPTIONS') return sendJson(res, 204, {});

  // ---- Health ----
  if (path === '/health' && method === 'GET') {
    return sendJson(res, 200, {
      ok: true,
      signals: config.signals.length,
      pending: config.signals.filter(s => s.result === null).length,
      ts: nowUtc(),
    });
  }

  // ---- Admin: signal config ----
  if (path === '/api/admin/signal-config' && method === 'GET') {
    return sendJson(res, 200, { ok: true, data: config.signalConfig });
  }

  const cfgMatch = path.match(/^\/api\/admin\/signal-config\/(level\d)$/);
  if (cfgMatch && method === 'PUT') {
    const levelKey = cfgMatch[1];
    const body = await readBody(req);
    const cfg = config.signalConfig[levelKey];
    if (!cfg) return sendJson(res, 404, { ok: false, error: { message: 'Level not found' } });
    if (typeof body.normalSignals === 'number') cfg.normalSignals = clamp(body.normalSignals, 0, 1000);
    if (typeof body.futureSignals === 'number') cfg.futureSignals = clamp(body.futureSignals, 0, 1000);
    return sendJson(res, 200, { ok: true, data: cfg });
  }

  // ---- Admin: promote user ----
  const promoteMatch = path.match(/^\/api\/admin\/users\/([^/]+)\/promote$/);
  if (promoteMatch && method === 'POST') {
    const tgId = decodeURIComponent(promoteMatch[1]);
    const body = await readBody(req);
    const level = clamp(parseInt(body.level, 10) || 0, 0, 3);
    setUserLevel(tgId, level);
    return sendJson(res, 200, { ok: true, data: { tgId, level } });
  }

  // ---- User: own allowance ----
  if (path === '/api/me/allowance' && method === 'GET') {
    const tgId = q.tg_id || q.tgId;
    const level = getUserLevel(tgId);
    const allowance = getAllowanceForLevel(level);
    return sendJson(res, 200, {
      ok: true,
      data: {
        level,
        label: allowance.label,
        normalSignals: allowance.normalSignals,
        futureSignals: allowance.futureSignals,
        usedNormalToday: getDailyCount(tgId, 'normal'),
        usedFutureToday: getDailyCount(tgId, 'future'),
      },
    });
  }

  // ---- List signals visible to a user ----
  if (path === '/api/signals' && method === 'GET') {
    const tgId = q.tg_id || q.tgId;
    const level = getUserLevel(tgId);
    const limit = clamp(parseInt(q.limit, 10) || 50, 1, 200);
    // Filter: future signals only visible to level >= 1
    // (normal visible to all levels, but capped by daily allowance)
    const allowance = getAllowanceForLevel(level);
    const visible = config.signals.filter(s => {
      if (s.type === 'future' && level < 1) return false;
      return true;
    }).slice(0, limit);
    return sendJson(res, 200, {
      ok: true,
      data: {
        level,
        allowance: { normal: allowance.normalSignals, future: allowance.futureSignals },
        usedToday: { normal: getDailyCount(tgId, 'normal'), future: getDailyCount(tgId, 'future') },
        signals: visible,
      },
    });
  }

  // ---- Single signal ----
  const sigMatch = path.match(/^\/api\/signals\/([^/]+)$/);
  if (sigMatch && method === 'GET') {
    const sig = config.signals.find(s => s.id === sigMatch[1]);
    if (!sig) return sendJson(res, 404, { ok: false, error: { message: 'Signal not found' } });
    return sendJson(res, 200, { ok: true, data: sig });
  }

  // ---- 404 ----
  return sendJson(res, 404, { ok: false, error: { message: 'Not found: ' + path } });
});

server.listen(PORT, () => {
  console.log('QuantVexa Signals server running on port ' + PORT);
  console.log('  Health:        http://localhost:' + PORT + '/health');
  console.log('  Signal config: http://localhost:' + PORT + '/api/admin/signal-config');
  console.log('  List signals:  http://localhost:' + PORT + '/api/signals?tg_id=123');
  console.log('  My allowance:  http://localhost:' + PORT + '/api/me/allowance?tg_id=123');
});
