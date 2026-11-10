// Phone console for edge-sim. Zero dependencies: Node built-ins only.
//
//   npm run build:native
//   node web/server.mjs          # then open http://localhost:3100
//
// Every request shells out to the same edge-sim binary the agent tools use,
// so what you see here is the real simulator state.
import { execFile } from "node:child_process";
import { mkdir, readFile } from "node:fs/promises";
import { createServer } from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(here, "..");
const SIM = process.env.EDGE_SIM || path.join(projectRoot, "native/build/edge-sim");
const WORLD =
  process.env.EDGE_WORLD || path.join(os.tmpdir(), "edge-sentry-web", "world.json");
const PORT = Number(process.env.PORT) || 3100;

// Must start with a letter or digit so an id can never be read as a CLI flag.
const ID_RE = /^[A-Za-z0-9][A-Za-z0-9-]{0,23}$/;
const ACTIONS = new Set(["move", "jam", "launch", "cease"]);
const UNIT_TYPES = new Set(["sensor", "relay", "jammer", "jet", "missile", "hq"]);
const TEAMS = new Set(["blue", "red"]);
const MAX_TICKS = 50;
const MAX_BODY_BYTES = 4096;

function runSim(args) {
  return new Promise((resolve) => {
    execFile(
      SIM,
      ["--world", WORLD, ...args],
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

// One command at a time: every call rewrites the same world.json.
let queue = Promise.resolve();
function sim(args) {
  const result = queue.then(() => runSim(args));
  queue = result.catch(() => {});
  return result;
}

function isInt(v, min, max) {
  return Number.isInteger(v) && v >= min && v <= max;
}

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
  if (req.method === "GET" && route === "/api/state") {
    return send(res, 200, await sim(["status"]));
  }
  if (req.method !== "POST") return send(res, 405, { ok: false, error: "Use POST" });

  let body;
  try {
    body = await readJsonBody(req);
  } catch (err) {
    return bad(res, err.message);
  }

  if (route === "/api/reset") return send(res, 200, await sim(["reset"]));

  if (route === "/api/tick") {
    const n = body.n ?? 1;
    if (!isInt(n, 1, MAX_TICKS)) return bad(res, `n must be a whole number from 1 to ${MAX_TICKS}`);
    return send(res, 200, await sim(["tick", "--n", String(n)]));
  }

  if (route === "/api/order") {
    const { id, action, tx, ty, target } = body;
    if (typeof id !== "string" || !ID_RE.test(id)) return bad(res, "Unknown unit id");
    if (!ACTIONS.has(action)) return bad(res, "action must be move, jam, launch or cease");
    const args = ["order", "--id", id, "--action", action];
    if (tx !== undefined || ty !== undefined) {
      if (!isInt(tx, 0, 255) || !isInt(ty, 0, 255)) return bad(res, "tx and ty must be map coordinates");
      args.push("--tx", String(tx), "--ty", String(ty));
    }
    if (target !== undefined) {
      if (typeof target !== "string" || !ID_RE.test(target)) return bad(res, "Unknown target id");
      args.push("--target", target);
    }
    return send(res, 200, await sim(args));
  }

  if (route === "/api/spawn") {
    const { type, team, x, y } = body;
    if (!UNIT_TYPES.has(type)) return bad(res, "Unknown unit type");
    if (!TEAMS.has(team)) return bad(res, "team must be blue or red");
    if (!isInt(x, 0, 255) || !isInt(y, 0, 255)) return bad(res, "x and y must be map coordinates");
    return send(
      res,
      200,
      await sim(["spawn", "--type", type, "--team", team, "--x", String(x), "--y", String(y)]),
    );
  }

  return send(res, 404, { ok: false, error: "No such endpoint" });
}

const server = createServer(async (req, res) => {
  try {
    const route = new URL(req.url ?? "/", "http://localhost").pathname;
    if (route.startsWith("/api/")) return await handleApi(req, res, route);
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

await mkdir(path.dirname(WORLD), { recursive: true });
server.listen(PORT, "0.0.0.0", () => {
  console.log(`edge-sentry phone console on http://localhost:${PORT}`);
  console.log(`simulator: ${SIM}`);
  console.log(`world file: ${WORLD}`);
});
