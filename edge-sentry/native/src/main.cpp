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
      << "  edge-sim --world <path> order --id ID --action <move|jam|launch|cease> [--tx N --ty N] [--target ID]\n";
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
    if (!ns.empty()) n = std::max(1, std::stoi(ns));
    for (int i = 0; i < n; ++i) world.tickOnce();
  } else if (cmd == "spawn") {
    UnitType type = UnitType::Sensor;
    Team team = Team::Blue;
    if (!unitTypeFromString(argValue(args, "--type"), type)) err = "bad --type";
    else if (!teamFromString(argValue(args, "--team"), team)) err = "bad --team";
    else {
      int x = std::stoi(argValue(args, "--x"));
      int y = std::stoi(argValue(args, "--y"));
      if (!world.spawn(type, team, x, y, &err)) {
        // err set
      } else {
        world.scoreAllTracks();
      }
    }
  } else if (cmd == "order") {
    OrderAction action = OrderAction::None;
    const std::string id = argValue(args, "--id");
    if (!orderFromString(argValue(args, "--action"), action)) err = "bad --action";
    else {
      int tx = -1, ty = -1;
      const std::string txs = argValue(args, "--tx");
      const std::string tys = argValue(args, "--ty");
      if (!txs.empty()) tx = std::stoi(txs);
      if (!tys.empty()) ty = std::stoi(tys);
      const std::string target = argValue(args, "--target");
      world.issueOrder(id, action, tx, ty, target, &err);
      world.scoreAllTracks();
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