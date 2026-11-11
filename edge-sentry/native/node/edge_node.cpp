// edge-node: the autonomy planner as a standalone process for a small
// computer (Raspberry Pi, Jetson, any Linux box) on the network.
//
// It speaks newline-delimited JSON. Each line in is one Observation (what
// `edge-sim observe` prints under "observation"); each line out is the plan:
// the orders to give this tick, with the time it took to decide.
//
//   edge-node --listen 7700          TCP server on this machine only, one client at a time
//   edge-node --listen 7700 --bind 0.0.0.0     accept connections from the network (no
//                                    authentication: use on a network you trust)
//   edge-node --stdin                read observations on stdin, write plans on stdout
//   edge-node --variant nolead ...   a planner variant, for comparisons
//
// It keeps nothing between messages: everything it needs is in the
// observation. A node that restarts mid-mission picks up on the next tick.

#include "autonomy.hpp"

#include <arpa/inet.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <sys/socket.h>
#include <unistd.h>

#include <chrono>
#include <csignal>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>

namespace {
constexpr size_t kMaxLine = 1 << 20;  // 1 MB: far above any real observation

std::string handle(const std::string& line, const PlannerConfig& cfg) {
  if (line.find("\"ping\"") != std::string::npos) return "{\"ok\":true,\"pong\":1,\"node\":\"edge-node\",\"v\":1}";
  Observation seen;
  if (!observationFromJson(line, seen)) return "{\"ok\":false,\"error\":\"not a version 1 observation\"}";
  const auto start = std::chrono::steady_clock::now();
  const std::vector<PlanOrder> orders = planDefense(seen, cfg);
  const double micros = std::chrono::duration<double, std::micro>(std::chrono::steady_clock::now() - start).count();
  return planToJson(seen.tick, orders, micros);
}

bool sendAll(int fd, const std::string& data) {
  size_t sent = 0;
  while (sent < data.size()) {
    const ssize_t n = ::send(fd, data.data() + sent, data.size() - sent, 0);
    if (n <= 0) return false;
    sent += static_cast<size_t>(n);
  }
  return true;
}

void serveClient(int fd, const PlannerConfig& cfg) {
  std::string buffer;
  char chunk[4096];
  for (;;) {
    const ssize_t n = ::recv(fd, chunk, sizeof(chunk), 0);
    if (n <= 0) return;
    buffer.append(chunk, static_cast<size_t>(n));
    if (buffer.size() > kMaxLine) return;  // a client that never sends a newline
    size_t end;
    while ((end = buffer.find('\n')) != std::string::npos) {
      const std::string line = buffer.substr(0, end);
      buffer.erase(0, end + 1);
      if (line.empty()) continue;
      if (!sendAll(fd, handle(line, cfg) + "\n")) return;
    }
  }
}

int listenOn(const std::string& bindAddr, int port) {
  const int server = ::socket(AF_INET, SOCK_STREAM, 0);
  if (server < 0) return -1;
  int yes = 1;
  ::setsockopt(server, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));
  sockaddr_in addr{};
  addr.sin_family = AF_INET;
  addr.sin_port = htons(static_cast<uint16_t>(port));
  if (::inet_pton(AF_INET, bindAddr.c_str(), &addr.sin_addr) != 1) return -1;
  if (::bind(server, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0) return -1;
  if (::listen(server, 4) != 0) return -1;
  return server;
}
}  // namespace

int main(int argc, char** argv) {
  std::string variant, bindAddr = "127.0.0.1";  // local only unless --bind says otherwise
  int port = 0;
  bool useStdin = false;
  for (int i = 1; i < argc; ++i) {
    const std::string a = argv[i];
    const bool more = i + 1 < argc;
    if (a == "--stdin") useStdin = true;
    else if (a == "--listen" && more) port = std::atoi(argv[++i]);
    else if (a == "--bind" && more) bindAddr = argv[++i];
    else if (a == "--variant" && more) variant = argv[++i];
    else {
      std::cerr << "usage: edge-node (--listen PORT [--bind ADDR] | --stdin) [--variant full|nolead|notimed|nopatrol|freefire]\n";
      return 2;
    }
  }
  PlannerConfig cfg;
  if (!plannerConfigFromName(variant, cfg)) {
    std::cerr << "bad --variant (expected full, nolead, notimed, nopatrol or freefire)\n";
    return 2;
  }

  if (useStdin) {
    std::string line;
    while (std::getline(std::cin, line)) {
      if (!line.empty()) std::cout << handle(line, cfg) << std::endl;
    }
    return 0;
  }
  if (port <= 0 || port > 65535) {
    std::cerr << "give --listen PORT (1..65535) or --stdin\n";
    return 2;
  }
  std::signal(SIGPIPE, SIG_IGN);
  const int server = listenOn(bindAddr, port);
  if (server < 0) {
    std::cerr << "could not listen on " << bindAddr << ":" << port << ": " << std::strerror(errno) << "\n";
    return 1;
  }
  std::cerr << "edge-node listening on " << bindAddr << ":" << port << std::endl;
  for (;;) {
    const int client = ::accept(server, nullptr, nullptr);
    if (client < 0) continue;
    int yes = 1;
    ::setsockopt(client, IPPROTO_TCP, TCP_NODELAY, &yes, sizeof(yes));
    serveClient(client, cfg);
    ::close(client);
  }
}
