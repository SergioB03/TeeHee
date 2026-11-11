#!/usr/bin/env node
// The console's Basic view: what a first-time visitor is told, and whether it is true.
//
//     node web/test_basic.mjs
//
// No dependencies. Three parts:
//
//  1. The sentences. web/index.html keeps everything that turns simulator output into words in one
//     marked block with no page and no network in it. This lifts that block out and runs it on
//     hand-made rounds, then on three real battles played through the real server and simulator, to
//     check that every hit, block and failed lock gets a sentence and that no unit id or simulator
//     term ever reaches one. It also checks the words against the simulator's own tables: every unit
//     type has a name, and what Basic says each aircraft can do is what the C++ says it can do.
//
//  2. The choice of level. The few lines in the page's head that pick Basic or Advanced before
//     anything is drawn are run against every combination of link, saved choice and blocked storage.
//
//  3. The real page in a real browser, when Chrome or Chromium is installed (CI requires it, with
//     EDGE_BROWSER=required): a phone-sized first visit, a watched battle, an order given by tapping,
//     and the switch to Advanced and back. Driven over Chrome's own debugging socket.
import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.dirname(here);
const page = readFileSync(path.join(here, "index.html"), "utf8");
const failures = [];
function check(name, ok, detail = "") {
  console.log((ok ? "PASS  " : "FAIL  ") + name + (ok || !detail ? "" : "  -> " + detail));
  if (!ok) failures.push(name);
}
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// ---- 1. the sentences -----------------------------------------------------------------------
const START = "// ---- plain words: start ----", END = "// ---- plain words: end ----";
const from = page.indexOf(START), to = page.indexOf(END);
if (from < 0 || to < from) {
  console.log("FAIL  web/index.html has no plain-words block to check");
  process.exit(1);
}
const words = new Function(page.slice(from, to) + `
  return { CEIL, PLAIN, HITS_AIR, canHit, plainKind, plainTitle, plainName, aboutKind, strengthNow,
           roundStory, quietStory, openingStory, resultStory, aboutUnit, LEGEND, MAP_NOTES, SCORE_NOTES };`)();

// Anything that names a unit the way the simulator does, or uses a word only the console's Advanced view explains.
const ID = /\b[BR]-[A-Z]+\d*\b/;
const TERM = /\btick|integrity|mission value|autopilot|\bHQ\b|hostile|\bSNR\b|\bcontact\b|point defense|firing solution|expended/i;
const said = new Set();   // every sentence produced anywhere in this file, for the leak check at the end
const keep = (lines) => { for (const l of lines) said.add(typeof l === "string" ? l : l.text); return lines; };

const unit = (id, type, team, x, y, more = {}) => ({ id, type, team, x, y, px: x, py: y, z: 0, pz: 0, hp: 100, integrity: 1,
  alive: true, targetId: "", order: { action: "none", targetId: "" }, ...more });
// The line-up a game starts from, so the fixtures name units the way a real battle does.
const cast = () => [
  unit("B-HQ", "hq", "blue", 1, 1), unit("B-S1", "sensor", "blue", 2, 3), unit("B-S2", "sensor", "blue", 3, 8),
  unit("B-R1", "relay", "blue", 4, 5), unit("B-F1", "stealth", "blue", 2, 6, { z: 4 }),
  unit("R-J1", "jammer", "red", 9, 8), unit("R-JET1", "jet", "red", 10, 2, { z: 3 }), unit("R-F1", "stealth", "red", 11, 10, { z: 4 }),
  unit("R-BMR1", "bomber", "red", 11, 0, { z: 4 }), unit("R-DRN1", "drone", "red", 8, 11, { z: 1 }),
  unit("R-MSL1", "missile", "red", 8, 3, { z: 2 }), unit("R-MSL2", "missile", "red", 11, 6, { z: 2 }),
];
const round = (events, change = () => {}, tick = 5) => {
  const units = cast();
  change(Object.fromEntries(units.map((u) => [u.id, u])));
  return { tick, units, events: events.map(([kind, detail, at]) => ({ tick: at ?? tick, kind, detail })) };
};
const told = (world, me, limit) => keep(words.roundStory(world, me, limit)).map((l) => l.text);

// The simulator's own tables, read from the C++.
const cpp = readFileSync(path.join(root, "native/src/world.cpp"), "utf8");
const simTypes = [...cpp.matchAll(/case UnitType::\w+: return "(\w+)";/g)].map((m) => m[1]);
const profiles = {};
for (const m of readFileSync(path.join(root, "native/src/profiles.hpp"), "utf8").matchAll(/static const AirProfile (\w+)\s*=\s*\{([^}]*)\}/g)) {
  const [speed, climb, ceiling, cruise, release, signature, damage, hitFactor, airToAir] = m[2].split(",").map((v) => v.trim());
  if (m[1] !== "ground") profiles[m[1]] = { speed: +speed, ceiling: +ceiling, signature: +signature, damage: +damage, airToAir: airToAir === "true" };
}
const flying = Object.keys(profiles);

check("every unit type the simulator has gets a plain name and a description",
  simTypes.length >= 9 && simTypes.every((t) => words.PLAIN[t] && words.aboutKind("red", t).length > 20 && words.aboutKind("blue", t).length > 20),
  simTypes.filter((t) => !words.PLAIN[t]).join(","));
check("the page's list of aircraft and their ceilings is the simulator's",
  flying.length === 5 && flying.every((t) => words.CEIL[t] === profiles[t].ceiling) && Object.keys(words.CEIL).length === flying.length);
check("which aircraft may attack aircraft is the simulator's own table",
  flying.every((t) => words.HITS_AIR.has(t) === profiles[t].airToAir) && words.HITS_AIR.size === flying.filter((t) => profiles[t].airToAir).length);
{
  const about = (t) => keep([words.aboutKind("red", t)])[0];
  const others = (t) => flying.filter((o) => o !== t);
  check("what Basic says about each aircraft is true of the simulator's numbers",
    /Nothing hits harder/.test(about("missile")) && others("missile").every((o) => profiles.missile.damage > profiles[o].damage)
    && /hits hard/.test(about("bomber")) && ["jet", "stealth", "drone"].every((o) => profiles.bomber.damage > profiles[o].damage)
    && /Easy for radar to spot/.test(about("bomber")) && profiles.bomber.signature > 1
    && /hard for radar to see/.test(about("stealth")) && profiles.stealth.signature < 0.5
    && /^Fast/.test(about("stealth")) && others("stealth").every((o) => profiles.stealth.speed > profiles[o].speed)
    && /hard for radar to see/.test(about("drone")) && profiles.drone.signature < 1
    && /flies low/.test(about("drone")) && others("drone").every((o) => profiles.drone.ceiling < profiles[o].ceiling)
    && /hits are light/.test(about("drone")) && others("drone").every((o) => profiles.drone.damage < profiles[o].damage)
    && /only attack things on the ground/.test(about("bomber")) && !profiles.bomber.airToAir
    && /only attack things on the ground/.test(about("drone")) && !profiles.drone.airToAir
    && /aircraft/.test(about("jet")) && profiles.jet.airToAir);
}
check("the legend covers every kind of unit a battle starts with",
  cast().every((u) => words.LEGEND[u.team].includes(u.type)) && words.MAP_NOTES.length >= 5 && words.SCORE_NOTES.length >= 2);
keep(words.MAP_NOTES.map((n) => n[1]));
keep(words.SCORE_NOTES);
for (const team of ["blue", "red"]) for (const type of words.LEGEND[team]) keep([words.aboutKind(team, type), words.plainTitle({ team, type })]);

check("a hit is told as who hit whom, by name",
  told(round([["engage", "R-JET1 hit B-R1"]]), null).join("|") === "The red jet hit blue's radio tower.");
check("a unit there are two of is one of them, not the only one",
  told(round([["engage", "R-MSL1 hit B-S2"]]), null).join("|") === "A red missile hit one of blue's radars.");
check("the blow that finishes an aircraft is a shoot-down, and one that finishes a ground unit destroys it",
  told(round([["engage", "B-F1 hit R-BMR1"], ["destroyed", "R-BMR1"]]), null).join("|") === "Blue's fighter shot down the red bomber."
  && told(round([["engage", "R-BMR1 hit B-R1"], ["destroyed", "B-R1"]]), null).join("|") === "The red bomber destroyed blue's radio tower.");
check("when two blows land on one unit in a round, only the one that finished it is the shoot-down",
  told(round([["engage", "R-JET1 hit B-F1"], ["engage", "R-F1 hit B-F1"], ["destroyed", "B-F1"]]), null).join("|")
    === "The red jet hit blue's fighter.|The red stealth jet shot down blue's fighter.");
check("a stopped missile is credited to the shield, and a blocked aircraft is a blocked hit",
  told(round([["blocked", "R-MSL2 blocked by B-HQ"], ["expended", "R-MSL2"]]), null).join("|") === "Blue's shield stopped a red missile."
  && told(round([["blocked", "R-JET1 blocked by B-HQ"]]), null).join("|") === "Blue's shield blocked a hit from the red jet.");
check("a failed lock is said plainly, before the hit it let through",
  told(round([["nolock", "B-HQ had no firing solution on R-F1"], ["engage", "R-F1 hit B-F1"]]), null).join("|")
    === "Blue's shield could not lock onto the red stealth jet.|The red stealth jet hit blue's fighter.");
check("orders, spent missiles and the scenario note are not part of the story",
  told(round([["order", "B-F1 launch"], ["expended", "R-MSL1"], ["scenario", "skirmish loaded: nobody has orders yet"]]), null).length === 0);
check("an earlier round's events are not told again",
  told(round([["engage", "R-JET1 hit B-R1", 4], ["engage", "R-DRN1 hit B-S2"]]), null).join("|") === "The red drone hit one of blue's radars.");
check("an event that names a unit the world does not have is skipped, not guessed at",
  told(round([["engage", "R-X9 hit B-R1"], ["blocked", "R-MSL1 blocked by B-Q"]]), null).length === 0);
{
  const events = [["blocked", "R-MSL1 blocked by B-HQ"], ["engage", "R-MSL2 hit B-HQ"], ["engage", "B-F1 hit R-JET1"], ["destroyed", "R-JET1"]];
  check("the same round reads as yours to a defender",
    told(round(events), "blue").join("|") === "Your shield stopped a red missile.|A red missile hit your base.|Your fighter shot down the red jet.");
  check("and as yours to an attacker",
    told(round(events), "red").join("|") === "Blue's shield stopped one of your missiles.|One of your missiles hit blue's base.|Blue's fighter shot down your jet.");
  const lines = keep(words.roundStory(round(events), null));
  check("each line carries the side that did it and the unit it happened to, for the map's flags",
    lines.map((l) => `${l.team}:${l.what}:${l.on}:${l.air}`).join("|") === "blue:blocked:B-HQ:false|red:hit:B-HQ:false|blue:lost:R-JET1:true");
}
{
  const seven = ["R-JET1 hit B-R1", "R-F1 hit B-F1", "R-BMR1 hit B-HQ", "R-DRN1 hit B-S2", "R-MSL1 hit B-S1", "B-F1 hit R-F1", "R-MSL2 hit B-HQ"].map((d) => ["engage", d]);
  const short = told(round(seven), null), all = told(round(seven), null, Infinity);
  check("a round with more than five things to tell says how many more, and the map still gets them all",
    short.length === 5 && short[4] === "And 3 more." && all.length === 7);
}
{
  const world = round([], (u) => { u["B-F1"].targetId = "R-BMR1"; u["B-HQ"].order = { action: "defend", targetId: "R-MSL1" }; u["R-BMR1"].targetId = "B-HQ"; u["R-JET1"].targetId = "B-R1"; });
  const lines = keep(words.quietStory(world, null)).map((l) => l.text);
  check("a quiet round still says who blue is after, what the shield is on, and how close red is",
    lines.join("|") === "Blue's fighter is chasing the red bomber.|Blue's shield is aimed at a red missile.|The red jet is 7 squares from blue's radio tower.", lines.join("|"));
  const close = round([], (u) => { u["R-DRN1"].targetId = "B-S2"; u["R-DRN1"].x = 3; u["R-DRN1"].y = 9; });
  check("an attacker that has arrived is right on top of its target",
    keep(words.quietStory(close, null)).map((l) => l.text).join("|") === "The red drone is right on top of one of blue's radars.");
  const jammed = round([], (u) => { u["B-HQ"].integrity = 0.55; u["B-S1"].integrity = 0.7; u["R-BMR1"].targetId = "B-HQ"; });
  check("a quiet round says when the jammer is what is costing blue",
    keep(words.quietStory(jammed, null)).map((l) => l.text).includes("The red jammer is weakening 2 of blue's units.")
    && keep(words.quietStory(jammed, "blue")).map((l) => l.text).includes("The red jammer is weakening 2 of your units.")
    && keep(words.quietStory(jammed, "red")).map((l) => l.text).includes("Your jammer is weakening 2 of blue's units."));
  const idle = round([]);
  check("a side with no orders is told how to give one",
    /Your aircraft are waiting for orders\. Tap one/.test(keep(words.quietStory(idle, "red")).map((l) => l.text).join(" "))
    && /Your fighter and your shield have no orders\. Tap a red unit/.test(keep(words.quietStory(idle, "blue")).map((l) => l.text).join(" "))
    && keep(words.quietStory(idle, null)).length === 1);
}
{
  const [watch, blue, red] = [words.openingStory("blue", true), words.openingStory("blue", false), words.openingStory("red", false)].map((l) => keep(l).map((x) => x.text).join(" "));
  check("before the first round each kind of visitor is told what to do",
    /computer is playing both sides/.test(watch) && /You are defending\. Tap a red unit/.test(blue) && /You are attacking\. Tap one of your aircraft/.test(red)
    && /Next round/.test(blue) && /Next round/.test(red));
}
{
  const game = (side, v, more = {}) => ({ side, missionValue: v, limit: 20, hits: 7, blocked: 1, shotDown: 3, lost: 0, ...more });
  const tell = (...args) => { const r = words.resultStory(...args); keep([r.title, r.scoreLine, r.stats, r.aside]); return r; };
  const watched = tell(game("blue", 0.789), true, null), defended = tell(game("blue", 0.789), false, null), attacked = tell(game("red", 0.789), false, null);
  check("the result reads differently for a spectator, a defender and an attacker",
    watched.title === "Blue held." && watched.scoreLine === "Blue kept 79% of its strength, averaged over the 20 rounds."
    && defended.title === "You held." && defended.scoreLine === "You kept 79% of your strength, averaged over the 20 rounds."
    && attacked.title === "Blue held you off." && attacked.scoreLine === "You left blue with 79% of its strength, averaged over the 20 rounds.");
  check("the result's counts are the game's counts, with one and none said as words would",
    watched.stats === "Red landed 7 hits. Blue's shield stopped 1 attack, blue destroyed 3 red units, and blue lost no units."
    && tell(game("red", 0.5, { hits: 1, blocked: 0, shotDown: 1, lost: 2 }), false, null).stats
      === "You landed 1 hit. Blue's shield stopped no attacks, blue destroyed 1 unit of yours, and blue lost 2 units.");
  check("the verdict follows the score, in opposite directions for the two sides",
    [0.9, 0.8, 0.7, 0.5].map((v) => tell(game("blue", v), false, null).title).join("|") === "You held firm.|You held.|You took a beating.|You were overrun."
    && [0.9, 0.8, 0.7, 0.5].map((v) => tell(game("red", v), false, null).title).join("|") === "Blue barely felt it.|Blue held you off.|You hurt blue badly.|You broke through."
    && tell(game("blue", 0.5), true, null).title === "Blue was overrun.");
  check("a watched battle's score is offered as the one to beat, and beating it is only claimed when it happened",
    /computer's score.*more than 79%/.test(watched.aside)
    && /Watch a battle to see what the computer scores/.test(defended.aside)
    && tell(game("blue", 0.81), false, 0.789).aside === "When you watched the computer defend, it kept 79%. You beat it."
    && tell(game("blue", 0.70), false, 0.789).aside === "When you watched the computer defend, it kept 79%."
    && tell(game("blue", 0.791), false, 0.789).aside === "When you watched the computer defend, it kept 79%. You matched it."
    && tell(game("red", 0.70), false, 0.789).aside === "When you watched the computer attack, blue kept 79%. You beat it."
    && tell(game("red", 0.85), false, 0.789).aside === "When you watched the computer attack, blue kept 79%.");
}
{
  const world = round([], (u) => {
    u["R-BMR1"].targetId = "B-HQ"; u["B-HQ"].order = { action: "defend", targetId: "R-BMR1" }; u["B-HQ"].integrity = 0.62; u["B-HQ"].hp = 60;
    u["R-MSL1"].alive = false; u["R-JET1"].alive = false; u["B-S1"].alive = false; u["R-DRN1"].order = { action: "orbit", targetId: "" };
  });
  const card = (id, me) => { const c = words.aboutUnit(world.units.find((u) => u.id === id), world, me); keep([c.title, c.about, ...c.state]); return c; };
  check("a tapped unit's card names it, says what it is for and what it is doing",
    card("R-BMR1", null).title === "Red bomber" && card("R-BMR1", "blue").state.join(" ") === "Right now it is going after your base."
    && card("B-HQ", null).state.join(" ") === "Its shield is aimed at the red bomber. It is being jammed and is working at 62%." && card("B-HQ", null).health === 60
    && card("R-DRN1", null).state.join(" ") === "Right now it is circling." && card("R-F1", "red").state.join(" ") === "Right now it has no orders."
    && card("B-F1", null).title === "Blue fighter" && /shoots them down/.test(card("B-F1", null).about));
  check("a card for something lost says how it went",
    card("R-MSL1", null).state.join("") === "It is gone." && card("R-JET1", null).state.join("") === "It has been shot down."
    && card("B-S1", null).state.join("") === "It has been destroyed." && card("B-S1", null).health === 0);
}
check("a bomber or a drone cannot be sent at an aircraft, and anything can be sent at the ground",
  !words.canHit({ type: "bomber" }, { type: "stealth" }) && !words.canHit({ type: "drone" }, { type: "jet" })
  && words.canHit({ type: "jet" }, { type: "stealth" }) && words.canHit({ type: "missile" }, { type: "bomber" }) && words.canHit({ type: "bomber" }, { type: "hq" }));

// Real battles, through the real server and simulator.
const freePort = () => new Promise((resolve, reject) => {
  const probe = net.createServer();
  probe.once("error", reject);
  probe.listen(0, "127.0.0.1", () => { const { port } = probe.address(); probe.close(() => resolve(port)); });
});
const port = await freePort();
const base = `http://127.0.0.1:${port}`;
const workdir = mkdtempSync(path.join(os.tmpdir(), "edge-sentry-basic-"));
const server = spawn(process.execPath, ["web/server.mjs"], { cwd: root, stdio: "ignore",
  env: { ...process.env, PORT: String(port), EDGE_WORLD: path.join(workdir, "world.json") } });
let chrome = null;
const cleanUp = () => {
  if (chrome) { chrome.close(); chrome = null; }
  if (server.exitCode === null) server.kill();
  try { rmSync(workdir, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 }); } catch { /* left for the system to clear */ }
};
process.on("exit", cleanUp);

const call = async (sid, route, body) => {
  const res = await fetch(base + route, body === undefined ? { headers: { "x-session": sid } }
    : { method: "POST", headers: { "content-type": "application/json", "x-session": sid }, body: JSON.stringify(body) });
  return res.json();
};
let up = false;
for (let i = 0; i < 100 && !up; i++) {
  try { up = (await call("basicprobe0001", "/api/state")).ok === true; } catch { await sleep(100); }
}
if (!up) {
  console.log("FAIL  the web server did not start (has edge-sim been built? npm run build:native)");
  process.exit(1);
}

// Play a whole battle and narrate every round of it the way the Basic view would.
async function battle(sid, side, me, { autopilot = false, orders = [] } = {}) {
  let r = await call(sid, "/api/game/start", { side });
  if (autopilot) r = await call(sid, "/api/game/autopilot", { on: true });
  for (const body of orders) r = await call(sid, "/api/order", body);
  const out = { events: 0, lines: 0, flags: 0, missing: [], silent: [], strength: 0, game: null, lost: 0 };
  for (let i = 0; i < 20; i++) {
    r = await call(sid, "/api/tick", { n: 1 });
    if (!r.ok) throw new Error(`tick failed: ${r.error}`);
    const world = r.world;
    const now = world.events.filter((e) => e.tick === world.tick);
    const want = now.filter((e) => e.kind === "engage" || e.kind === "blocked" || e.kind === "nolock").length;
    const story = keep(words.roundStory(world, me, Infinity));
    out.events += want;
    out.lines += story.length;
    out.flags += story.filter((l) => l.on && world.units.some((u) => u.id === l.on)).length;
    out.lost += story.filter((l) => l.what === "lost").length;
    if (story.length !== want) out.missing.push(`round ${world.tick}: ${want} events, ${story.length} sentences`);
    if (now.filter((e) => e.kind === "destroyed").length !== story.filter((l) => l.what === "lost").length) out.missing.push(`round ${world.tick}: a loss was not told`);
    const shown = keep(words.roundStory(world, me));
    if (!shown.length && !keep(words.quietStory(world, me)).length) out.silent.push(world.tick);
    for (const u of world.units) { const c = words.aboutUnit(u, world, me); keep([c.title, c.about, ...c.state]); }
    out.strength += words.strengthNow(world);
    out.game = r.game;
  }
  const end = words.resultStory(out.game, autopilot, 0.8);
  keep([end.title, end.scoreLine, end.stats, end.aside]);
  return out;
}
try {
  const everything = (target) => ["R-JET1", "R-F1", "R-BMR1", "R-DRN1", "R-MSL1", "R-MSL2"].map((id) => ({ id, action: "launch", target }));
  const watched = await battle("basicwatch00001", "blue", null, { autopilot: true });
  const attacked = await battle("basicattack0001", "red", "red", { orders: everything("B-HQ") });
  const undefended = await battle("basicdefend0001", "blue", "blue");
  const all = [watched, attacked, undefended];
  check("in a real battle the computer plays, every hit, block and failed lock the simulator reports gets a sentence",
    watched.missing.length === 0 && watched.events >= 8 && watched.lines === watched.events, watched.missing[0] ?? `${watched.events} events`);
  check("the same holds when a visitor attacks as red and when one leaves blue undefended",
    attacked.missing.length === 0 && undefended.missing.length === 0 && attacked.events >= 8 && undefended.events >= 8,
    attacked.missing[0] ?? undefended.missing[0] ?? `${attacked.events} and ${undefended.events} events`);
  check("those battles included shoot-downs and losses, and each was told as one",
    all.reduce((n, b) => n + b.lost, 0) >= 3 && all.reduce((n, b) => n + b.game.shotDown + b.game.lost, 0) === all.reduce((n, b) => n + b.lost, 0),
    `${all.map((b) => b.lost).join("+")} told, ${all.map((b) => b.game.shotDown + b.game.lost).join("+")} happened`);
  check("every one of those sentences names a unit that is on the map to be flagged", all.every((b) => b.flags === b.lines));
  check("no round in any of them left the visitor with nothing to read", all.every((b) => b.silent.length === 0));
  check("blue's strength, averaged over a real battle, is the score the server reports",
    all.every((b) => b.game.over && Math.abs(b.strength / 20 - b.game.missionValue) < 1e-9), all.map((b) => `${b.strength / 20} vs ${b.game.missionValue}`).join(", "));
  check("the three battles really were different: the computer defended better than nobody did",
    watched.game.missionValue > undefended.game.missionValue && attacked.game.missionValue !== watched.game.missionValue);
  const leaks = [...said].filter((s) => ID.test(s) || TERM.test(s));
  check("no sentence the Basic view said in any of this contains a unit id or a simulator term", said.size > 100 && leaks.length === 0, leaks[0] ?? `only ${said.size} sentences`);
  // Titles ("Red stealth jet") are not sentences; anything longer is, and must read like one.
  const odd = [...said].filter((s) => !s || !/^[A-Z0-9]/.test(s) || /undefined|null|NaN|\[object|  /.test(s) || (s.split(" ").length > 3 && !/[.!]$/.test(s)));
  check("every sentence starts with a capital, ends with a full stop and has no gaps in it", odd.length === 0, odd[0]);

  // ---- 2. the choice of level --------------------------------------------------------------
  const headScript = page.match(/<head>[\s\S]*?<script>([\s\S]*?)<\/script>[\s\S]*?<\/head>/);
  const choose = (search, kept, storage = "works") => {
    let level = null;
    const document = { documentElement: { setAttribute: (name, value) => { if (name === "data-level") level = value; } } };
    const localStorage = { getItem: () => { if (storage === "blocked") throw new Error("blocked"); return kept; } };
    new Function("document", "location", "localStorage", headScript[1])(document, { search }, localStorage);
    return level;
  };
  check("the level is chosen in the page's head, before anything is drawn",
    Boolean(headScript) && page.indexOf(headScript[1]) < page.indexOf("<style>") && page.indexOf(headScript[1]) < page.indexOf("<body>"));
  check("a first visit gets Basic", choose("", null) === "basic");
  check("a returning visitor gets the level they last switched to", choose("", "advanced") === "advanced" && choose("", "basic") === "basic");
  check("a link can ask for either level, and wins over the saved choice",
    choose("?level=advanced", null) === "advanced" && choose("?level=advanced", "basic") === "advanced" && choose("?level=basic", "advanced") === "basic");
  check("anything else in the link or in storage falls back to Basic", choose("?level=expert", null) === "basic" && choose("", "expert") === "basic" && choose("?level=", "junk") === "basic");
  check("with storage blocked the page still opens, in Basic unless the link says otherwise",
    choose("", null, "blocked") === "basic" && choose("?level=advanced", null, "blocked") === "advanced");
  check("each level hides the other's blocks with one rule apiece",
    page.includes('html[data-level="basic"] .only-advanced { display: none !important; }') && page.includes('html[data-level="advanced"] .only-basic { display: none !important; }'));

  // ---- 3. the real page in a real browser ---------------------------------------------------
  const required = process.env.EDGE_BROWSER === "required";
  const binary = process.env.CHROME_BIN || ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]
    .find((name) => spawnSync("sh", ["-c", `command -v ${name}`], { stdio: "ignore" }).status === 0);
  if (!binary || typeof WebSocket === "undefined") {
    const why = !binary ? "no Chrome or Chromium on this machine" : "this Node has no WebSocket client (needs Node 22)";
    if (required) check("a browser is available to open the real page (EDGE_BROWSER=required)", false, why);
    else console.log(`SKIP  the checks on the real page in a browser: ${why}`);
  } else {
    chrome = await launchChrome(binary);
    await browserChecks(chrome);
  }
} finally {
  cleanUp();
}

if (failures.length) {
  console.log(`${failures.length} check(s) FAILED`);
  process.exit(1);
}
console.log("all checks passed");
process.exit(0);

// A very small client for Chrome's debugging socket: enough to open a page, run script in it and wait.
async function launchChrome(binary) {
  const profile = mkdtempSync(path.join(os.tmpdir(), "edge-sentry-chrome-"));
  const proc = spawn(binary, ["--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", "--no-first-run",
    "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"], { stdio: ["ignore", "ignore", "pipe"] });
  const address = await new Promise((resolve, reject) => {
    let log = "";
    const timer = setTimeout(() => reject(new Error("Chrome did not start: " + log.slice(-300))), 30000);
    proc.stderr.on("data", (chunk) => {
      log += chunk;
      const found = log.match(/DevTools listening on (ws:\/\/\S+)/);
      if (found) { clearTimeout(timer); resolve(found[1]); }
    });
    proc.once("exit", (code) => { clearTimeout(timer); reject(new Error(`Chrome exited with ${code}: ` + log.slice(-300))); });
  });
  const socket = new WebSocket(address);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = () => reject(new Error("could not reach Chrome's debugging socket")); });
  let nextId = 0;
  const waiting = new Map(), problems = new Map();
  socket.onmessage = (message) => {
    const data = JSON.parse(message.data);
    if (data.id && waiting.has(data.id)) {
      const { resolve, reject } = waiting.get(data.id);
      waiting.delete(data.id);
      if (data.error) reject(new Error(data.error.message)); else resolve(data.result);
    } else if (data.method === "Runtime.exceptionThrown") {
      problems.get(data.sessionId)?.push(data.params.exceptionDetails.exception?.description ?? data.params.exceptionDetails.text);
    }
  };
  const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    const id = ++nextId;
    waiting.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params, sessionId }));
  });
  // Each visitor is a separate browser context: its own storage, so its own session and saved level.
  const visitor = async ({ width = 390, height = 844, mobile = true } = {}) => {
    const { browserContextId } = await send("Target.createBrowserContext");
    const open = async (url) => {
      const { targetId } = await send("Target.createTarget", { url: "about:blank", browserContextId });
      const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
      const errors = [];
      problems.set(sessionId, errors);
      await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 2, mobile }, sessionId);
      if (mobile) await send("Emulation.setTouchEmulationEnabled", { enabled: true }, sessionId);
      await send("Page.enable", {}, sessionId);
      await send("Runtime.enable", {}, sessionId);
      const run = async (expression) => {
        const { result, exceptionDetails } = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }, sessionId);
        if (exceptionDetails) throw new Error(exceptionDetails.exception?.description ?? exceptionDetails.text);
        return result.value;
      };
      const until = async (expression, ms = 15000) => {
        const deadline = Date.now() + ms;
        for (;;) {
          try { if (await run(expression)) return true; } catch { /* the page is between documents; try again */ }
          if (Date.now() > deadline) throw new Error(`timed out waiting for: ${expression}`);
          await sleep(50);
        }
      };
      const go = async (to) => {
        await send("Page.navigate", { url: to }, sessionId);
        await until(`document.readyState === "complete" && typeof world !== "undefined" && world !== null && !busy`);
      };
      await go(url);
      return { run, until, go, errors, reload: () => go(url) };
    };
    return { open };
  };
  const close = () => {
    try { socket.close(); } catch { /* already gone */ }
    proc.kill("SIGKILL");
    // Chrome may still be writing to its profile as it dies; a leftover temp folder is not a test failure.
    try { rmSync(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 }); } catch { /* left for the system to clear */ }
  };
  return { visitor, close };
}

async function browserChecks(browser) {
  // What the visitor can see: an element that is laid out and not hidden by either level's rule.
  const helpers = `
    const seen = (id) => { const el = document.getElementById(id); return Boolean(el) && getComputedStyle(el).display !== "none" && el.getBoundingClientRect().width > 0; };
    const names = () => [...document.querySelectorAll("#map3d .labels text")].map((t) => t.textContent);
    const reading = () => document.body.innerText + "\\n" + names().join("\\n");
    const tapUnit = (id) => document.querySelector('#map3d [data-id="' + id + '"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    const lines = () => [...document.querySelectorAll("#bCaption li")].map((li) => li.textContent);
  `;
  const phone = await (await browser.visitor()).open(base + "/");
  const run = (page, body) => page.run(`(() => { ${helpers} ${body} })()`);
  const idPattern = String(ID), termPattern = String(TERM);

  check("browser: a first visit on a phone opens in Basic, with three ways in and none of the console",
    await run(phone, `return document.documentElement.getAttribute("data-level") === "basic" && ["bIdle", "bWatch", "bDefend", "bAttack", "map3d", "bLegend"].every(seen)
      && !["tick", "reset", "modeIdle", "view2d", "tick1", "tick5", "contacts", "log", "decisions", "sensors", "addUnit", "hint", "map", "clock"].some(seen);`));
  check("browser: the map names every unit in a word, and nothing on the page is a unit id or a simulator term",
    await run(phone, `const n = names(); return n.length >= 7 && n.every((t) => /^[A-Z][a-z]+( [a-z]+)?$/.test(t)) && !${idPattern}.test(reading()) && !${termPattern}.test(reading());`),
    await run(phone, `return names().join("|");`));
  check("browser: nothing spills off the side of a phone screen", await run(phone, `return document.documentElement.scrollWidth <= window.innerWidth + 1;`));

  await run(phone, `document.getElementById("bWatch").click();`);
  await phone.until(`game && game.autopilot === true && !busy`);
  check("browser: Watch a battle hands both sides to the computer and shows the scoreboard",
    await run(phone, `return game.side === "blue" && game.tick === 0 && seen("bGame") && seen("clock") && !seen("bIdle") && !seen("bChoices") && /computer is playing both sides/.test(lines()[0]);`));
  await phone.until(`game.tick >= 1`, 20000);
  check("browser: the rounds then advance without a tap", await run(phone, `return game.tick >= 1 && document.getElementById("bPlay").textContent === "Pause";`));
  // Press Pause as a visitor would: the button is disabled for the instant a round is being fetched.
  await phone.until(`(() => { const b = document.getElementById("bPlay"); if (playing && !b.disabled) b.click(); return !playing; })()`);
  await phone.until(`!busy && glideT === 1`);
  const heldAt = await run(phone, `return game.tick;`);
  await sleep(3000);
  check("browser: Pause holds them", await run(phone, `return game.tick === ${heldAt} && document.getElementById("bPlay").textContent === "Auto-play";`));
  // Step on until a round with something in it, and compare what is written and flagged with what happened.
  let matched = null;
  for (let i = 0; i < 14 && !matched; i++) {
    const before = await run(phone, `return world.tick;`);
    await run(phone, `document.getElementById("bNext").click();`);
    await phone.until(`world.tick === ${before + 1} && !busy && glideT === 1`);
    matched = await run(phone, `const now = world.events.filter((e) => e.tick === world.tick && ["engage", "blocked", "nolock"].includes(e.kind)).length;
      return now ? { now, told: lines().length, flags: document.querySelectorAll("#map3d .callout").length, clean: !${idPattern}.test(reading()) && !${termPattern}.test(reading()) } : null;`);
  }
  check("browser: a round where something landed is told in as many sentences, flagged in as many places, in plain words",
    Boolean(matched) && matched.told === matched.now && matched.flags === matched.now && matched.clean, JSON.stringify(matched));
  await run(phone, `tapUnit("B-HQ");`);
  check("browser: tapping the base swaps the story for a card about it, with no orders while the computer plays",
    await run(phone, `return seen("bCard") && !seen("bCaption") && document.querySelector("#bCard h2").textContent === "Blue base" && document.querySelectorAll("#bCard .acts button").length === 0;`));

  // A second visitor, defending by hand at the same time.
  const hand = await (await browser.visitor()).open(base + "/");
  await run(hand, `document.getElementById("bDefend").click();`);
  await hand.until(`game && game.side === "blue" && game.tick === 0 && !busy`);
  await run(hand, `tapUnit("R-BMR1");`);
  check("browser: a defender who taps a red aircraft is offered the fighter and the shield",
    await run(hand, `return [...document.querySelectorAll("#bCard .acts button")].map((b) => b.textContent).join("|") === "Send fighter|Aim shield";`));
  await run(hand, `document.querySelector("#bCard .acts button").click();`);
  await hand.until(`!busy && note !== null`);
  const sent = await call(await run(hand, `return SESSION;`), "/api/state");
  check("browser: Send fighter reaches the simulator as an order, and is confirmed in a sentence",
    sent.world.units.find((u) => u.id === "B-F1").targetId === "R-BMR1" && (await run(hand, `return lines()[0];`)) === "Your fighter is chasing the red bomber.");
  check("browser: the two visitors' battles are separate", (await run(phone, `return game.tick;`)) > 0 && sent.game.tick === 0 && sent.game.autopilot === false);

  // The switch.
  await run(hand, `document.getElementById("levelAdvanced").click();`);
  check("browser: Advanced shows the whole console on the same battle and hides Basic",
    await run(hand, `return document.documentElement.getAttribute("data-level") === "advanced" && ["tick", "reset", "modeGame", "view2d", "tick1", "contacts", "decisions", "hint"].every(seen)
      && !["bGame", "bCaption", "bChoices", "bNext", "bLegend"].some(seen) && /You command blue\\. Tick 0 of 20/.test(document.getElementById("gameStatus").textContent)
      && names().some((t) => /^B-F1/.test(t)) && document.querySelectorAll("#map3d .callout").length === 0;`));
  check("browser: Advanced fits a phone screen too", await run(hand, `return document.documentElement.scrollWidth <= window.innerWidth + 1;`));
  await hand.reload();
  check("browser: the level is remembered across a reload", await run(hand, `return document.documentElement.getAttribute("data-level") === "advanced" && seen("tick1");`));
  await run(hand, `document.getElementById("levelBasic").click();`);
  check("browser: switching back returns to Basic with the battle where it was",
    await run(hand, `return document.documentElement.getAttribute("data-level") === "basic" && seen("bGame") && !seen("tick1") && document.getElementById("bRound").textContent === "Before round 1";`));
  const linked = await (await browser.visitor({ width: 1280, height: 800, mobile: false })).open(base + "/?level=advanced");
  check("browser: a link with ?level=advanced opens the console directly, on a wide screen",
    await run(linked, `return document.documentElement.getAttribute("data-level") === "advanced" && seen("modeIdle") && seen("tick1") && !seen("bIdle");`));
  await run(linked, `document.getElementById("levelBasic").click();`);
  check("browser: using the switch takes over from the link",
    await run(linked, `return !location.search.includes("level") && document.documentElement.getAttribute("data-level") === "basic" && seen("bIdle");`));
  const errors = [...phone.errors, ...hand.errors, ...linked.errors];
  check("browser: no page raised a script error", errors.length === 0, errors[0]);
}
