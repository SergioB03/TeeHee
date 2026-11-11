#include "world.hpp"
#include "json_mini.hpp"

#include <ctime>
#include <map>
#include <set>

#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

namespace {
void usage() {
  std::cerr
      << "edge-sim — abstract terrain EW / air sandbox (game math only)\n"
      << "Usage:\n"
      << "  edge-sim --world <path> status\n"
      << "  edge-sim --world <path> reset\n"
      << "  edge-sim --world <path> tick [--n N]\n"
      << "  edge-sim --world <path> score\n"
      << "  edge-sim --world <path> spawn --type <sensor|relay|jammer|hq|jet|stealth|bomber|drone|missile> --team <blue|red> --x N --y N [--z N]\n"
      << "  edge-sim --world <path> order --id ID --action <move|altitude|orbit|jam|launch|cease|defend> [--tx N --ty N] [--tz N] [--target ID]\n"
      << "      altitude levels run 0 (ground) to 5; aircraft types differ in speed, climb, ceiling and signature\n"
      << "  edge-sim --world <path> decisions [--n N]     last N entries of the decision log (default 20)\n"
      << "  edge-sim --world <path> reset --scenario skirmish   larger setup with no orders, used by play mode\n"
      << "  edge-sim --world <path> opponent --side <red|blue> [--doctrine coordinated|basic]\n"
      << "                                                let the computer give that side's orders for this tick. Red's default\n"
      << "                                                doctrine times its strike to arrive together, comes in low, jams the\n"
      << "                                                command post and goes for blue's fighter; basic is the original script\n"
      << "  edge-sim --world <path> observe               the observation message blue's autonomy gets (no red orders or targets)\n"
      << "  edge-sim --world <path> autopilot [--variant full|nolead|notimed|nopatrol|freefire|nodefense|gohome|untaught|closein|commit] [--dry-run]\n"
      << "                                                run the autonomy planner for blue and give its orders\n"
      << "  edge-sim --world <path> sensing --noise F [--seed N] [--fusion on|off]\n"
      << "                                                0 = perfect picture (default); up to 1 = sensors miss contacts and\n"
      << "                                                report noisy positions, and blue sees its tracker's estimate.\n"
      << "                                                --fusion off drops the tracker (best single detection only)\n"
      << "  edge-sim inputs                               names of the learned model's inputs, in order\n"
      << "  edge-sim serve [--scorer S]                   keep the world in memory and read one command per line on stdin\n"
      << "                                                (the same words, separated by tabs; `load<TAB>{json}` sets the world).\n"
      << "                                                For training loops: no process or file per command\n"
      << "Options:\n"
      << "  --scorer <learned|heuristic|sequence>   which threat scorer fills threatScore (default learned)\n"
      << "  --actor <name>                 who is issuing this command, recorded in the decision log (default cli)\n"
      << "  --reason <text>                why, recorded in the decision log\n"
      << "  --no-log                       do not write to the decision log (bulk rollouts)\n"
      << "  --no-world                     leave the world out of the reply\n"
      << "The decision log is <world path>.decisions.jsonl: one JSON line per reset, spawn and order,\n"
      << "with the threat picture the decision was made on.\n";
}

std::string readFile(const std::string& path) {
  std::ifstream in(path);
  if (!in) return {};
  std::ostringstream ss;
  ss << in.rdbuf();
  return ss.str();
}

bool writeFile(const std::string& path, const std::string& data) {
  std::ofstream out(path);
  if (!out) return false;
  out << data;
  return static_cast<bool>(out);
}

World loadOrInit(const std::string& path) {
  const std::string raw = readFile(path);
  if (raw.empty()) {
    World w = World::defaultScenario();
    writeFile(path, w.toJson(true));
    return w;
  }
  return World::fromJson(raw);
}

// Where the world lives between commands: a file (one process per command),
// or this process's memory (`edge-sim serve`).
struct Store {
  std::string path;       // the world file; empty when serving from memory
  World* live = nullptr;  // the world itself when serving
  World load() const { return live ? *live : loadOrInit(path); }
  bool save(const World& w) const {
    if (!live) return writeFile(path, w.toJson(true));
    *live = w;
    return true;
  }
};

// Parsed command line. Options that take a value consume the next token, so
// a value can never be mistaken for a flag (a reason of "--no-log" or a unit
// id of "--scorer" stays plain text).
struct Args {
  std::map<std::string, std::string> values;
  std::set<std::string> flags;
  std::string command;
};

bool isCommand(const std::string& a) {
  return a == "status" || a == "reset" || a == "tick" || a == "score" || a == "spawn" || a == "order" ||
         a == "decisions" || a == "inputs" || a == "opponent" || a == "observe" || a == "autopilot" || a == "sensing" ||
         a == "serve";
}

Args parseArgs(const std::vector<std::string>& raw) {
  static const std::set<std::string> kTakesValue = {
      "--world", "--scorer", "--actor", "--reason", "--n",      "--type", "--team", "--scenario", "--side", "--variant", "--noise", "--seed", "--fusion", "--doctrine",
      "--x",     "--y",      "--z",     "--id",     "--action", "--tx",   "--ty",   "--tz",       "--target"};
  Args out;
  for (size_t i = 0; i < raw.size(); ++i) {
    const std::string& a = raw[i];
    if (kTakesValue.count(a)) {
      out.flags.insert(a);
      if (i + 1 < raw.size()) out.values[a] = raw[++i];
    } else if (a.rfind("-", 0) == 0) {
      out.flags.insert(a);
    } else if (out.command.empty() && isCommand(a)) {
      out.command = a;
    }
  }
  return out;
}

std::string argValue(const Args& args, const std::string& key) {
  const auto it = args.values.find(key);
  return it == args.values.end() ? std::string() : it->second;
}

bool hasFlag(const Args& args, const std::string& key) {
  return args.flags.count(key) > 0;
}

constexpr int kMaxTicksPerCall = 1000;
constexpr size_t kMaxReasonChars = 300;

std::string nowUtc() {
  const std::time_t t = std::time(nullptr);
  char buf[32];
  std::strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", std::gmtime(&t));
  return buf;
}

bool validActor(const std::string& s) {
  if (s.empty() || s.size() > 24) return false;
  for (char c : s) {
    if (!(std::isalnum(static_cast<unsigned char>(c)) || c == '-' || c == '_')) return false;
  }
  return true;
}

// The threat picture a decision was made on: one entry per tracked contact
// with its label, score and the model inputs an operator would point to.
std::string pictureJson(const World& w) {
  static const char* const kShown[] = {"proximity", "closing", "heading", "intercept", "altitude", "descent", "blueInField", "recentFire", "stopTicks"};
  const auto& names = modelInputNames();
  std::vector<std::string> out;
  for (const auto& t : w.tracks) {
    std::ostringstream o;
    o << "{\"id\":" << jsonString(t.sourceUnitId)
      << ",\"label\":" << jsonString(t.threat.label)
      << ",\"score\":" << t.threat.score
      << ",\"scorer\":" << jsonString(t.threat.scorer)
      << ",\"inputs\":{";
    bool first = true;
    for (const char* key : kShown) {
      for (size_t i = 0; i < names.size() && i < t.threat.observed.size(); ++i) {
        if (names[i] != key) continue;
        o << (first ? "" : ",") << jsonString(key) << ":" << t.threat.observed[i];
        first = false;
      }
    }
    o << "}}";
    out.push_back(o.str());
  }
  return "[" + joinComma(out) + "]";
}

// One decision-log line. `argsJson` is the already-built {"id":...} object.
void appendDecision(const std::string& logPath, int tick, const std::string& actor, const std::string& command,
                    const std::string& argsJson, const std::string& reason, const std::string& err,
                    const std::string& picture) {
  std::ostringstream entry;
  entry << "{\"at\":" << jsonString(nowUtc())
        << ",\"tick\":" << tick
        << ",\"actor\":" << jsonString(actor)
        << ",\"command\":" << jsonString(command)
        << ",\"args\":" << argsJson
        << ",\"reason\":" << jsonString(reason)
        << ",\"ok\":" << (err.empty() ? "true" : "false")
        << ",\"error\":" << jsonString(err)
        << ",\"picture\":" << picture << "}\n";
  std::ofstream log(logPath, std::ios::app);
  if (log) log << entry.str();
}

std::string lastLines(const std::string& path, int n) {
  std::ifstream in(path);
  std::vector<std::string> lines;
  std::string line;
  while (std::getline(in, line)) {
    if (!line.empty()) lines.push_back(line);
  }
  const size_t start = lines.size() > static_cast<size_t>(n) ? lines.size() - static_cast<size_t>(n) : 0;
  return joinComma(std::vector<std::string>(lines.begin() + static_cast<long>(start), lines.end()));
}

// Whole-string integer parse. Returns false instead of throwing so bad CLI
// input becomes a JSON error rather than an abort.
bool parseInt(const std::string& s, int& out) {
  if (s.empty()) return false;
  try {
    size_t used = 0;
    const int v = std::stoi(s, &used);
    if (used != s.size()) return false;
    out = v;
    return true;
  } catch (...) {
    return false;
  }
}
// Run one command against the world in `store` and write the one-line JSON
// reply to `out`. Returns the process exit code the command line gives.
int run(const Args& args, const Store& store, std::ostream& out) {
  const std::string cmd = args.command;
  if (cmd.empty()) {
    std::cerr << "{\"error\":\"missing command\"}\n";
    usage();
    return 2;
  }

  // No decision log when the world is served from memory: there is no file to keep it beside.
  const std::string logPath = store.path.empty() ? std::string() : store.path + ".decisions.jsonl";
  if (cmd == "decisions") {
    int n = 20;
    const std::string ns = argValue(args, "--n");
    if (!ns.empty() && (!parseInt(ns, n) || n < 1 || n > 500)) {
      out << "{\"ok\":false,\"error\":\"bad --n (expected integer 1..500)\"}\n";
      return 1;
    }
    out << "{\"ok\":true,\"command\":\"decisions\",\"decisions\":[" << lastLines(logPath, n) << "]}\n";
    return 0;
  }

  std::string actor = "cli";
  if (hasFlag(args, "--actor")) {
    actor = argValue(args, "--actor");
    if (!validActor(actor)) {
      out << "{\"ok\":false,\"error\":\"bad --actor (letters, digits, - and _, up to 24)\"}\n";
      return 1;
    }
  }
  std::string reason = argValue(args, "--reason");
  if (reason.size() > kMaxReasonChars) reason.resize(kMaxReasonChars);

  World world = store.load();
  std::string err;
  // Decisions are judged against the picture they were made on, so capture
  // it before the command changes anything.
  const bool isDecision = (cmd == "reset" || cmd == "spawn" || cmd == "order") && !hasFlag(args, "--no-log") && !logPath.empty();
  const int tickBefore = world.tick;
  const std::string pictureBefore = isDecision ? pictureJson(world) : std::string();

  std::string opponentOrders;  // JSON array, filled by the opponent command
  if (cmd == "reset") {
    const std::string scenario = argValue(args, "--scenario");
    // A reset starts the scenario again; how blue senses is a setting and carries over.
    const double keepNoise = world.noise;
    const int keepSeed = world.seed;
    const bool keepFusion = world.fusion;
    if (scenario.empty() || scenario == "default") world.reset();
    else if (scenario == "skirmish") world = World::skirmishScenario();
    else err = "bad --scenario (expected default or skirmish)";
    if (err.empty() && keepNoise > 0) {
      world.noise = keepNoise;
      world.seed = keepSeed;
      world.fusion = keepFusion;
      world.scoreAllTracks();
    }
  } else if (cmd == "opponent" || cmd == "autopilot") {
    Team side = Team::Blue;
    PlannerConfig variant;
    const bool autopilot = cmd == "autopilot";
    const std::string doctrine = argValue(args, "--doctrine");
    World::redCoordinated = doctrine != "basic";
    if (!autopilot && (!teamFromString(argValue(args, "--side"), side) || side == Team::Unknown)) {
      err = "bad --side (expected red or blue)";
    } else if (!doctrine.empty() && doctrine != "basic" && doctrine != "coordinated") {
      err = "bad --doctrine (expected coordinated or basic)";
    } else if (autopilot && !plannerConfigFromName(argValue(args, "--variant"), variant)) {
      err = "bad --variant (expected full, nolead, notimed, nopatrol, freefire, nodefense, gohome, untaught, closein or commit)";
    } else {
      // Each order is logged on its own line, as actor "opponent" or
      // "autopilot", against the picture the plan was made on. The doctrine
      // flown through `opponent` is logged as "autopilot" when the caller says
      // so (--actor autopilot): a player who hands their own side to the
      // computer is not their own opponent.
      const std::string picture = pictureJson(world);
      const std::string who = (autopilot || actor == "autopilot") ? "autopilot" : "opponent";
      std::vector<OpponentMove> plan;
      if (autopilot) {
        // Through the same JSON message an edge node receives, so the two
        // cannot decide differently.
        Observation seen;
        observationFromJson(observationToJson(world.observe()), seen);
        for (const auto& p : planDefense(seen, variant)) {
          OpponentMove m;
          m.id = p.id;
          orderFromString(p.action, m.action);
          m.tx = p.tx; m.ty = p.ty; m.tz = p.tz;
          m.targetId = p.target;
          m.reason = p.mode + ": " + p.reason;
          plan.push_back(m);
        }
      } else {
        plan = world.planOpponent(side);
      }
      const bool dryRun = hasFlag(args, "--dry-run");
      std::vector<std::string> issued;
      for (const auto& m : plan) {
        std::string moveErr;
        if (!dryRun) world.issueOrder(m.id, m.action, m.tx, m.ty, m.tz, m.targetId, &moveErr);
        std::ostringstream a;
        a << "{\"id\":" << jsonString(m.id) << ",\"action\":" << jsonString(orderToString(m.action));
        if (m.tx >= 0) a << ",\"tx\":\"" << m.tx << "\",\"ty\":\"" << m.ty << "\"";
        if (!m.targetId.empty()) a << ",\"target\":" << jsonString(m.targetId);
        a << "}";
        if (!hasFlag(args, "--no-log") && !dryRun && !logPath.empty()) {
          appendDecision(logPath, world.tick, who, "order", a.str(), m.reason, moveErr, picture);
        }
        std::ostringstream o;
        o << "{\"id\":" << jsonString(m.id) << ",\"action\":" << jsonString(orderToString(m.action))
          << ",\"target\":" << jsonString(m.targetId) << ",\"tx\":" << m.tx << ",\"ty\":" << m.ty << ",\"tz\":" << m.tz
          << ",\"reason\":" << jsonString(m.reason) << ",\"ok\":" << (moveErr.empty() ? "true" : "false") << "}";
        issued.push_back(o.str());
      }
      opponentOrders = "[" + joinComma(issued) + "]";
      world.scoreAllTracks();
    }
  } else if (cmd == "sensing") {
    double level = 0;
    int seedValue = world.seed;
    const std::string noiseText = argValue(args, "--noise"), seedText = argValue(args, "--seed"), fusionText = argValue(args, "--fusion");
    bool parsed = !noiseText.empty();
    try {
      size_t used = 0;
      if (parsed) { level = std::stod(noiseText, &used); parsed = used == noiseText.size(); }
    } catch (...) {
      parsed = false;
    }
    if (!parsed || level < 0 || level > 1) err = "bad --noise (expected a number from 0 to 1)";
    else if (!seedText.empty() && !parseInt(seedText, seedValue)) err = "bad --seed";
    else if (!fusionText.empty() && fusionText != "on" && fusionText != "off") err = "bad --fusion (expected on or off)";
    else {
      world.noise = level;
      world.seed = seedValue;
      if (!fusionText.empty()) world.fusion = fusionText == "on";
      // Changing how blue senses starts every track again from nothing.
      for (auto& u : world.units) {
        u.tracked = false;
        u.trkTick = -1;
        u.misses = 0;
        u.trk.clear();
      }
      world.scoreAllTracks();
    }
  } else if (cmd == "status" || cmd == "observe") {
    // no-op
  } else if (cmd == "score") {
    world.scoreAllTracks();
  } else if (cmd == "tick") {
    int n = 1;
    const std::string ns = argValue(args, "--n");
    if (!ns.empty() && (!parseInt(ns, n) || n < 1 || n > kMaxTicksPerCall)) {
      err = "bad --n (expected integer 1..1000)";
    } else {
      for (int i = 0; i < n; ++i) world.tickOnce();
    }
  } else if (cmd == "spawn") {
    UnitType type = UnitType::Sensor;
    Team team = Team::Blue;
    int x = 0, y = 0, z = -1;
    const std::string zs = argValue(args, "--z");
    if (!unitTypeFromString(argValue(args, "--type"), type)) err = "bad --type";
    else if (!teamFromString(argValue(args, "--team"), team)) err = "bad --team";
    else if (!parseInt(argValue(args, "--x"), x) || !parseInt(argValue(args, "--y"), y)) {
      err = "missing or bad --x/--y";
    } else if (!zs.empty() && (!parseInt(zs, z) || z < 0)) {
      err = "bad --z (expected an altitude level from 0)";
    } else if (world.spawn(type, team, x, y, z, &err)) {
      world.scoreAllTracks();
    }
  } else if (cmd == "order") {
    OrderAction action = OrderAction::None;
    const std::string id = argValue(args, "--id");
    int tx = -1, ty = -1, tz = -1;
    const std::string txs = argValue(args, "--tx");
    const std::string tys = argValue(args, "--ty");
    const std::string tzs = argValue(args, "--tz");
    if (!orderFromString(argValue(args, "--action"), action)) err = "bad --action";
    else if (!txs.empty() && !parseInt(txs, tx)) err = "bad --tx";
    else if (!tys.empty() && !parseInt(tys, ty)) err = "bad --ty";
    else if (!tzs.empty() && (!parseInt(tzs, tz) || tz < 0)) err = "bad --tz (expected an altitude level from 0)";
    else {
      const std::string target = argValue(args, "--target");
      if (world.issueOrder(id, action, tx, ty, tz, target, &err)) world.scoreAllTracks();
    }
  }

  if (isDecision) {
    std::ostringstream argsJson;
    argsJson << "{";
    static const char* const kLogged[] = {"--id", "--action", "--tx", "--ty", "--tz", "--target", "--type", "--team", "--x", "--y", "--z", "--scenario"};
    bool first = true;
    for (const char* key : kLogged) {
      if (!hasFlag(args, key)) continue;
      argsJson << (first ? "" : ",") << jsonString(std::string(key).substr(2)) << ":" << jsonString(argValue(args, key));
      first = false;
    }
    argsJson << "}";
    appendDecision(logPath, tickBefore, actor, cmd, argsJson.str(), reason, err, pictureBefore);
  }

  if (!err.empty()) {
    out << "{\"ok\":false,\"error\":\"" << err << "\"}\n";
    return 1;
  }

  if (!store.save(world)) {
    out << "{\"ok\":false,\"error\":\"failed to write world\"}\n";
    return 1;
  }

  out << "{\"ok\":true,\"command\":\"" << cmd << "\"";
  if (cmd == "opponent" || cmd == "autopilot") out << ",\"orders\":" << opponentOrders;
  if (cmd == "observe") {
    // blueValue is the score everything is judged on: health x integrity, averaged over
    // blue's units, lost ones counting as zero. It rides beside the observation, not in it:
    // a training loop needs it as the reward, and the autonomy is never shown it.
    double value = 0.0;
    int blue = 0;
    for (const auto& u : world.units) {
      if (u.team != Team::Blue) continue;
      ++blue;
      if (u.alive) value += (u.hp / 100.0) * u.integrity;
    }
    out << ",\"observation\":" << observationToJson(world.observe()) << ",\"blueValue\":" << (blue ? value / blue : 0.0);
  }
  // --no-world leaves the world out of the reply: a training loop that only wants the
  // observation or an acknowledgement does not pay to print and parse it.
  if (!hasFlag(args, "--no-world")) out << ",\"world\":" << world.toJson(false);
  out << "}\n";
  return 0;
}

// `edge-sim serve`: keep the world in memory and take one command per line on
// stdin, as the same words the command line takes, separated by tabs. Each
// command gets exactly one line of JSON on stdout. It exists for training
// loops and bulk rollouts, where starting a process and rewriting a file for
// every command costs a hundred times more than the command itself.
//
//   load<TAB>{...world json...}     replace the world (the same JSON a world file holds)
//   tick<TAB>--n<TAB>1              any ordinary command
//   quit                            stop (so does the end of input)
int serve() {
  World world = World::defaultScenario();
  const Store store{std::string(), &world};
  std::string line;
  while (std::getline(std::cin, line)) {
    if (!line.empty() && line.back() == '\r') line.pop_back();
    std::vector<std::string> words;
    size_t from = 0;
    for (;;) {
      const size_t tab = line.find('\t', from);
      words.push_back(line.substr(from, tab == std::string::npos ? std::string::npos : tab - from));
      if (tab == std::string::npos) break;
      from = tab + 1;
    }
    if (words[0].empty()) continue;
    if (words[0] == "quit") break;
    if (words[0] == "load") {
      if (words.size() != 2 || words[1].empty()) {
        std::cout << "{\"ok\":false,\"error\":\"load needs the world as one JSON value after a tab\"}" << std::endl;
      } else {
        world = World::fromJson(words[1]);
        std::cout << "{\"ok\":true,\"command\":\"load\"}" << std::endl;
      }
      continue;
    }
    const Args args = parseArgs(words);
    if (args.command.empty() || args.command == "serve" || args.command == "inputs") {
      std::cout << "{\"ok\":false,\"error\":\"unknown command\"}" << std::endl;
      continue;
    }
    run(args, store, std::cout);
    std::cout.flush();
  }
  return 0;
}
}  // namespace

int main(int argc, char** argv) {
  std::vector<std::string> raw;
  for (int i = 1; i < argc; ++i) raw.emplace_back(argv[i]);
  const Args args = parseArgs(raw);

  if (raw.empty() || hasFlag(args, "--help") || hasFlag(args, "-h")) {
    usage();
    return raw.empty() ? 2 : 0;
  }

  if (args.command == "inputs") {
    std::vector<std::string> names;
    for (const auto& n : modelInputNames()) names.push_back(jsonString(n));
    std::cout << "{\"ok\":true,\"command\":\"inputs\",\"inputs\":[" << joinComma(names)
              << "],\"sequenceInputs\":" << kSequenceInputs << "}\n";
    return 0;
  }

  const bool serving = args.command == "serve";
  const std::string worldPath = argValue(args, "--world");
  if (worldPath.empty() && !serving) {
    std::cerr << "{\"error\":\"missing --world path\"}\n";
    return 2;
  }

  // Must be set before the world loads: loading rescores every track.
  if (hasFlag(args, "--scorer")) {
    const std::string scorer = argValue(args, "--scorer");
    if (scorer == "learned") World::activeScorer = ScorerKind::Learned;
    else if (scorer == "heuristic") World::activeScorer = ScorerKind::Heuristic;
    else if (scorer == "sequence") World::activeScorer = ScorerKind::Sequence;
    else {
      std::cout << "{\"ok\":false,\"error\":\"bad --scorer (expected learned, heuristic or sequence)\"}\n";
      return 1;
    }
  }

  if (serving) return serve();
  return run(args, Store{worldPath}, std::cout);
}
