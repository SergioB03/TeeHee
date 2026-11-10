// Phone console for edge-sim. Zero dependencies: Node built-ins only.
//
//   npm run build:native
//   node web/server.mjs          # then open http://localhost:3100
//
// Every request shells out to the same edge-sim binary the agent tools use,
// so what you see here is the real simulator state.
//
// Each visitor gets their own sandbox: the page sends a random session id in
// the x-session header and the server keeps one world per session on disk.
// Requests without the header share a single default world.
import { execFile } from "node:child_process";
import { mkdir, readFile, readdir, rm, stat, unlink, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(here, "..");
const SIM = process.env.EDGE_SIM || path.join(projectRoot, "native/build/edge-sim");
// EDGE_WORLD, when set, is the default (no-session) world file.
const BASE = process.env.EDGE_WORLD
  ? path.dirname(process.env.EDGE_WORLD)
  : path.join(os.tmpdir(), "edge-sentry-web");
const DEFAULT_WORLD = process.env.EDGE_WORLD || path.join(BASE, "world.json");
const PORT = Number(process.env.PORT) || 3100;

// Must start with a letter or digit so an id can never be read as a CLI flag.
const ID_RE = /^[A-Za-z0-9][A-Za-z0-9-]{0,23}$/;
const SESSION_RE = /^[a-z0-9]{12,40}$/;
const ACTIONS = new Set(["move", "jam", "launch", "cease", "defend"]);
const UNIT_TYPES = new Set(["sensor", "relay", "jammer", "jet", "missile", "hq"]);
const TEAMS = new Set(["blue", "red"]);
const MAX_TICKS = 50;
const MAX_BODY_BYTES = 4096;
const MAX_SESSIONS = 300;
const SESSION_TTL_MS = 6 * 60 * 60 * 1000;
const GAME_TICKS = 20;

function runSim(world, actor, args) {
  return new Promise((resolve) => {
    execFile(
      SIM,
      ["--world", world, "--actor", actor, ...args],
      { maxBuffer: 2 * 1024 * 1024, timeout: 10_000 },
      (err, stdout, stderr) => {
        // edge-sim exits non-zero for rejected commands but still prints
        // {"ok":false,"error":...} on stdout, so parse before looking at err.
        try {
          resolve(JSON.parse(String(stdout)));
        } catch {
          const detail = err?.code === "ENOENT"
            ? "edge-sim binary not found. Build it with: npm run build:native"
            : String(stderr || err?.message || "no output").trim().slice(0, 200);
          resolve({ ok: false, error: `Simulator did not respond: ${detail}` });
        }
      },
    );
  });
}

// One request at a time: commands rewrite world files, and a game turn is
// several commands that must not interleave with another request.
let queue = Promise.resolve();
function exclusive(fn) {
  const result = queue.then(fn);
  queue = result.catch(() => {});
  return result;
}

function isInt(v, min, max) {
  return Number.isInteger(v) && v >= min && v <= max;
}

// ---- sessions -----------------------------------------------------------------

const sessionsDir = path.join(BASE, "sessions");

async function pruneSessions() {
  let names;
  try {
    names = await readdir(sessionsDir);
  } catch {
    return;
  }
  const entries = [];
  for (const name of names) {
    try {
      entries.push({ name, mtime: (await stat(path.join(sessionsDir, name))).mtimeMs });
    } catch {
      // vanished between readdir and stat
    }
  }
  entries.sort((a, b) => a.mtime - b.mtime);
  const now = Date.now();
  const excess = Math.max(0, entries.length - MAX_SESSIONS);
  for (let i = 0; i < entries.length; i++) {
    if (i < excess || now - entries[i].mtime > SESSION_TTL_MS) {
      await rm(path.join(sessionsDir, entries[i].name), { recursive: true, force: true });
    }
  }
}

/** Resolve the request's sandbox: { world, gameFile }. */
async function sessionFor(req) {
  const id = req.headers["x-session"];
  if (typeof id !== "string" || !SESSION_RE.test(id)) {
    return { world: DEFAULT_WORLD, gameFile: DEFAULT_WORLD + ".game.json" };
  }
  const dir = path.join(sessionsDir, id);
  try {
    await stat(dir);
  } catch {
    await pruneSessions();
    await mkdir(dir, { recursive: true });
  }
  return { world: path.join(dir, "world.json"), gameFile: path.join(dir, "game.json") };
}

// ---- play mode ----------------------------------------------------------------
// A game is 20 ticks of the skirmish scenario. The player commands one side;
// before every tick the simulator's `opponent` command gives the other side's
// orders (blue is run by the learned threat model, red by a fixed doctrine).

async function loadGame(session) {
  try {
    return JSON.parse(await readFile(session.gameFile, "utf8"));
  } catch {
    return null;
  }
}

async function saveGame(session, game) {
  await writeFile(session.gameFile, JSON.stringify(game));
}

function blueValue(world) {
  const blue = world.units.filter((u) => u.team === "blue");
  if (!blue.length) return 0;
  let total = 0;
  for (const u of blue) if (u.alive) total += (u.hp / 100) * u.integrity;
  return total / blue.length;
}

function publicGame(game) {
  if (!game) return null;
  return {
    side: game.side,
    tick: game.ticks,
    limit: game.limit,
    over: game.over,
    missionValue: game.ticks ? game.sum / game.ticks : 1,
    hits: game.hits,
    blocked: game.blocked,
    lost: game.lost,
  };
}

async function startGame(session, side) {
  const res = await runSim(session.world, "console", ["reset", "--scenario", "skirmish"]);
  if (!res.ok) return res;
  const game = { side, limit: GAME_TICKS, ticks: 0, sum: 0, hits: 0, blocked: 0, lost: 0, over: false };
  await saveGame(session, game);
  return { ...res, game: publicGame(game), opponentOrders: [] };
}

async function gameTick(session, game, n) {
  const other = game.side === "blue" ? "red" : "blue";
  const opponentOrders = [];
  let last = null;
  for (let i = 0; i < n && !game.over; i++) {
    const plan = await runSim(session.world, "opponent", ["opponent", "--side", other]);
    if (!plan.ok) return plan;
    for (const o of plan.orders ?? []) opponentOrders.push({ ...o, tick: plan.world.tick });
    last = await runSim(session.world, "console", ["tick", "--n", "1"]);
    if (!last.ok) return last;
    const world = last.world;
    game.ticks += 1;
    game.sum += blueValue(world);
    for (const e of world.events) {
      if (e.tick !== world.tick) continue;
      if (e.kind === "engage") game.hits += 1;
      if (e.kind === "blocked") game.blocked += 1;
    }
    game.lost = world.units.filter((u) => u.team === "blue" && !u.alive).length;
    if (game.ticks >= game.limit) game.over = true;
  }
  await saveGame(session, game);
  if (!last) last = await runSim(session.world, "console", ["status"]);
  return { ...last, game: publicGame(game), opponentOrders };
}

// ---- http ---------------------------------------------------------------------

function readJsonBody(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on("data", (chunk) => {
      size += chunk.length;
      // Past the limit, keep draining (so the client still gets a 400) but
      // stop buffering; cut the connection if it is far beyond reasonable.
      if (size > MAX_BODY_BYTES * 64) req.destroy();
      else if (size <= MAX_BODY_BYTES) chunks.push(chunk);
    });
    req.on("end", () => {
      if (size > MAX_BODY_BYTES) return reject(new Error("Request body is too large"));
      if (size === 0) return resolve({});
      try {
        const parsed = JSON.parse(Buffer.concat(chunks).toString("utf8"));
        if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
          throw new Error("not an object");
        }
        resolve(parsed);
      } catch {
        reject(new Error("Request body must be a JSON object"));
      }
    });
    req.on("error", reject);
  });
}

function send(res, status, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
  });
  res.end(body);
}

const bad = (res, error) => send(res, 400, { ok: false, error });

async function handleApi(req, res, route) {
  const session = await sessionFor(req);
  const game = await loadGame(session);
  const sim = (args) => runSim(session.world, "console", args);
  const withGame = (result) => (result.ok ? { ...result, game: publicGame(game) } : result);

  if (req.method === "GET" && route === "/api/state") {
    return send(res, 200, withGame(await sim(["status"])));
  }
  if (req.method === "GET" && route === "/api/decisions") {
    return send(res, 200, await sim(["decisions", "--n", "30"]));
  }
  if (req.method !== "POST") return send(res, 405, { ok: false, error: "Use POST" });

  let body;
  try {
    body = await readJsonBody(req);
  } catch (err) {
    return bad(res, err.message);
  }

  if (route === "/api/game/start") {
    if (!TEAMS.has(body.side)) return bad(res, "side must be blue or red");
    return send(res, 200, await startGame(session, body.side));
  }

  if (route === "/api/game/end") {
    await unlink(session.gameFile).catch(() => {});
    const result = await sim(["status"]);
    return send(res, 200, result.ok ? { ...result, game: null } : result);
  }

  if (route === "/api/reset") {
    // In a game, reset means "start this game over on the same side".
    if (game) return send(res, 200, await startGame(session, game.side));
    return send(res, 200, withGame(await sim(["reset"])));
  }

  if (route === "/api/tick") {
    const n = body.n ?? 1;
    if (!isInt(n, 1, MAX_TICKS)) return bad(res, `n must be a whole number from 1 to ${MAX_TICKS}`);
    if (game) {
      if (game.over) return bad(res, "This game is over. Start a new one to keep playing");
      return send(res, 200, await gameTick(session, game, n));
    }
    return send(res, 200, withGame(await sim(["tick", "--n", String(n)])));
  }

  if (route === "/api/order") {
    const { id, action, tx, ty, target, reason } = body;
    if (typeof id !== "string" || !ID_RE.test(id)) return bad(res, "Unknown unit id");
    if (!ACTIONS.has(action)) return bad(res, "action must be move, jam, launch, cease or defend");
    const args = ["order", "--id", id, "--action", action];
    if (tx !== undefined || ty !== undefined) {
      if (!isInt(tx, 0, 255) || !isInt(ty, 0, 255)) return bad(res, "tx and ty must be map coordinates");
      args.push("--tx", String(tx), "--ty", String(ty));
    }
    if (target !== undefined) {
      if (typeof target !== "string" || !ID_RE.test(target)) return bad(res, "Unknown target id");
      args.push("--target", target);
    }
    if (reason !== undefined) {
      if (typeof reason !== "string" || reason.length > 300) return bad(res, "reason must be text up to 300 characters");
      // Passed as one argv entry with no shell, so its content cannot become a flag or a command.
      if (reason.trim()) args.push("--reason", reason.trim());
    }
    if (game) {
      if (game.over) return bad(res, "This game is over. Start a new one to keep playing");
      // A player may only command their own side.
      const now = await sim(["status"]);
      const unit = now.ok ? now.world.units.find((u) => u.id === id) : null;
      if (!unit) return bad(res, "Unknown unit id");
      if (unit.team !== game.side) return bad(res, `You command ${game.side} in this game, and ${id} is not yours`);
    }
    return send(res, 200, withGame(await sim(args)));
  }

  if (route === "/api/spawn") {
    if (game) return bad(res, "Adding units is off during a game");
    const { type, team, x, y } = body;
    if (!UNIT_TYPES.has(type)) return bad(res, "Unknown unit type");
    if (!TEAMS.has(team)) return bad(res, "team must be blue or red");
    if (!isInt(x, 0, 255) || !isInt(y, 0, 255)) return bad(res, "x and y must be map coordinates");
    return send(
      res,
      200,
      withGame(await sim(["spawn", "--type", type, "--team", team, "--x", String(x), "--y", String(y)])),
    );
  }

  return send(res, 404, { ok: false, error: "No such endpoint" });
}

const server = createServer(async (req, res) => {
  try {
    const route = new URL(req.url ?? "/", "http://localhost").pathname;
    if (route.startsWith("/api/")) return await exclusive(() => handleApi(req, res, route));
    if (req.method === "GET" && (route === "/" || route === "/index.html")) {
      const html = await readFile(path.join(here, "index.html"));
      res.writeHead(200, {
        "content-type": "text/html; charset=utf-8",
        "cache-control": "no-cache",
      });
      return res.end(html);
    }
    res.writeHead(404, { "content-type": "text/plain; charset=utf-8" });
    res.end("Not found");
  } catch (err) {
    console.error(err);
    if (!res.headersSent) send(res, 500, { ok: false, error: "Server error" });
    else res.end();
  }
});

await mkdir(path.dirname(DEFAULT_WORLD), { recursive: true });
server.listen(PORT, "0.0.0.0", () => {
  console.log(`edge-sentry phone console on http://localhost:${PORT}`);
  console.log(`simulator: ${SIM}`);
  console.log(`worlds: ${BASE}`);
});
