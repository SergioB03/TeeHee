#include "world.hpp"

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
      << "  edge-sim --world <path> spawn --type <sensor|relay|jammer|jet|missile|hq> --team <blue|red> --x N --y N\n"
      << "  edge-sim --world <path> order --id ID --action <move|jam|launch|cease> [--tx N --ty N] [--target ID]\n"
      << "Options:\n"
      << "  --scorer <learned|heuristic>   which threat scorer fills threatScore (default learned)\n";
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

std::string argValue(const std::vector<std::string>& args, const std::string& key) {
  for (size_t i = 0; i + 1 < args.size(); ++i) {
    if (args[i] == key) return args[i + 1];
  }
  return {};
}

bool hasFlag(const std::vector<std::string>& args, const std::string& key) {
  for (const auto& a : args) if (a == key) return true;
  return false;
}

constexpr int kMaxTicksPerCall = 1000;

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
}  // namespace

int main(int argc, char** argv) {
  std::vector<std::string> args;
  for (int i = 1; i < argc; ++i) args.emplace_back(argv[i]);

  if (args.empty() || hasFlag(args, "--help") || hasFlag(args, "-h")) {
    usage();
    return args.empty() ? 2 : 0;
  }

  const std::string worldPath = argValue(args, "--world");
  if (worldPath.empty()) {
    std::cerr << "{\"error\":\"missing --world path\"}\n";
    return 2;
  }

  // Must be set before the world loads: loading rescores every track.
  if (hasFlag(args, "--scorer")) {
    const std::string scorer = argValue(args, "--scorer");
    if (scorer == "learned") World::activeScorer = ScorerKind::Learned;
    else if (scorer == "heuristic") World::activeScorer = ScorerKind::Heuristic;
    else {
      std::cout << "{\"ok\":false,\"error\":\"bad --scorer (expected learned or heuristic)\"}\n";
      return 1;
    }
  }

  std::string cmd;
  for (const auto& a : args) {
    if (a == "status" || a == "reset" || a == "tick" || a == "score" || a == "spawn" || a == "order") {
      cmd = a;
      break;
    }
  }
  if (cmd.empty()) {
    std::cerr << "{\"error\":\"missing command\"}\n";
    usage();
    return 2;
  }

  World world = loadOrInit(worldPath);
  std::string err;

  if (cmd == "reset") {
    world.reset();
  } else if (cmd == "status") {
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
    int x = 0, y = 0;
    if (!unitTypeFromString(argValue(args, "--type"), type)) err = "bad --type";
    else if (!teamFromString(argValue(args, "--team"), team)) err = "bad --team";
    else if (!parseInt(argValue(args, "--x"), x) || !parseInt(argValue(args, "--y"), y)) {
      err = "missing or bad --x/--y";
    } else if (world.spawn(type, team, x, y, &err)) {
      world.scoreAllTracks();
    }
  } else if (cmd == "order") {
    OrderAction action = OrderAction::None;
    const std::string id = argValue(args, "--id");
    int tx = -1, ty = -1;
    const std::string txs = argValue(args, "--tx");
    const std::string tys = argValue(args, "--ty");
    if (!orderFromString(argValue(args, "--action"), action)) err = "bad --action";
    else if (!txs.empty() && !parseInt(txs, tx)) err = "bad --tx";
    else if (!tys.empty() && !parseInt(tys, ty)) err = "bad --ty";
    else {
      const std::string target = argValue(args, "--target");
      if (world.issueOrder(id, action, tx, ty, target, &err)) world.scoreAllTracks();
    }
  }

  if (!err.empty()) {
    std::cout << "{\"ok\":false,\"error\":\"" << err << "\"}\n";
    return 1;
  }

  if (!writeFile(worldPath, world.toJson(true))) {
    std::cout << "{\"ok\":false,\"error\":\"failed to write world\"}\n";
    return 1;
  }

  std::cout << "{\"ok\":true,\"command\":\"" << cmd << "\",\"world\":" << world.toJson(false) << "}\n";
  return 0;
}