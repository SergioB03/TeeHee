// edge-node: the autonomy planner as a standalone process for a small
// computer (Raspberry Pi, Jetson, any Linux box) on the network.
//
// It speaks newline-delimited JSON. Each line in is one Observation (what
// `edge-sim observe` prints under "observation"); each line out is the plan:
// the orders to give this tick, with the time it took to decide.
//
//   edge-node --listen 7700          TCP server on this machine only
//   edge-node --listen 7700 --bind 0.0.0.0     accept connections from the network (no
//                                    authentication: use on a network you trust)
//   edge-node --stdin                read observations on stdin, write plans on stdout
//   edge-node --ping 127.0.0.1:7700  ask a running node whether it is alive; exit 0 if it answers
//                                    (a container health check with nothing else in the image)
//   edge-node --variant nolead ...   a planner variant, for comparisons
//
// The server is one thread that watches every connection with poll(), so a
// health probe or a second client is answered while another client stays
// connected, and an idle connection cannot starve the others. SIGTERM and
// SIGINT stop it at once: there is no state to save.
//
// It keeps nothing between messages: everything it needs is in the
// observation. A node that restarts mid-mission picks up on the next tick.

#include "autonomy.hpp"

#include <arpa/inet.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

#include <chrono>
#include <csignal>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>

namespace {
constexpr size_t kMaxLine = 1 << 20;  // 1 MB: far above any real observation
constexpr size_t kMaxClients = 32;    // connections served at once; more are turned away

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

struct Client {
  int fd;
  std::string buffer;
};

// Read what this client has sent and answer every complete line. False means
// the connection is finished (closed, broken, or sending a line without end).
bool serveReady(Client& c, const PlannerConfig& cfg) {
  char chunk[4096];
  const ssize_t n = ::recv(c.fd, chunk, sizeof(chunk), 0);
  if (n <= 0) return false;
  c.buffer.append(chunk, static_cast<size_t>(n));
  if (c.buffer.size() > kMaxLine) return false;  // a client that never sends a newline
  size_t end;
  while ((end = c.buffer.find('\n')) != std::string::npos) {
    const std::string line = c.buffer.substr(0, end);
    c.buffer.erase(0, end + 1);
    if (line.empty()) continue;
    if (!sendAll(c.fd, handle(line, cfg) + "\n")) return false;
  }
  return true;
}

// A client that stops reading must not hold the others up for long.
void setTimeouts(int fd, int seconds) {
  timeval tv{};
  tv.tv_sec = seconds;
  ::setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
}

bool parseHostPort(const std::string& text, std::string& host, int& port) {
  const size_t colon = text.rfind(':');
  host = colon == std::string::npos ? "127.0.0.1" : text.substr(0, colon);
  port = std::atoi((colon == std::string::npos ? text : text.substr(colon + 1)).c_str());
  return port > 0 && port <= 65535;
}

// Ask the node at host:port for a pong. 0 if it answers, 1 if not.
int pingNode(const std::string& host, int port) {
  const int fd = ::socket(AF_INET, SOCK_STREAM, 0);
  if (fd < 0) return 1;
  timeval tv{};
  tv.tv_sec = 2;
  ::setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
  ::setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
  sockaddr_in addr{};
  addr.sin_family = AF_INET;
  addr.sin_port = htons(static_cast<uint16_t>(port));
  bool alive = false;
  if (::inet_pton(AF_INET, host.c_str(), &addr.sin_addr) == 1 &&
      ::connect(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) == 0 && sendAll(fd, "{\"ping\":1}\n")) {
    std::string reply;
    char chunk[256];
    ssize_t n;
    while (reply.find('\n') == std::string::npos && reply.size() < 1024 && (n = ::recv(fd, chunk, sizeof(chunk), 0)) > 0)
      reply.append(chunk, static_cast<size_t>(n));
    alive = reply.find("\"pong\":1") != std::string::npos;
  }
  ::close(fd);
  return alive ? 0 : 1;
}

void stopNow(int) { ::_exit(0); }  // nothing to save: the planner keeps no state

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
  if (::listen(server, 16) != 0) return -1;
  return server;
}
}  // namespace

int main(int argc, char** argv) {
  std::string variant, ping, bindAddr = "127.0.0.1";  // local only unless --bind says otherwise
  int port = 0;
  bool useStdin = false;
  for (int i = 1; i < argc; ++i) {
    const std::string a = argv[i];
    const bool more = i + 1 < argc;
    if (a == "--stdin") useStdin = true;
    else if (a == "--listen" && more) port = std::atoi(argv[++i]);
    else if (a == "--bind" && more) bindAddr = argv[++i];
    else if (a == "--ping" && more) ping = argv[++i];
    else if (a == "--variant" && more) variant = argv[++i];
    else {
      std::cerr << "usage: edge-node (--listen PORT [--bind ADDR] | --stdin | --ping HOST:PORT) [--variant full|nolead|notimed|nopatrol|freefire|nodefense|gohome|untaught|closein|commit]\n";
      return 2;
    }
  }
  PlannerConfig cfg;
  if (!plannerConfigFromName(variant, cfg)) {
    std::cerr << "bad --variant (expected full, nolead, notimed, nopatrol, freefire, nodefense, gohome, untaught, closein or commit)\n";
    return 2;
  }

  if (!ping.empty()) {
    std::string host;
    int target = 0;
    if (!parseHostPort(ping, host, target)) {
      std::cerr << "give --ping HOST:PORT, for example --ping 127.0.0.1:7700\n";
      return 2;
    }
    return pingNode(host, target);
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
  // As the first process in a container the default action for these signals
  // is to ignore them, and `docker stop` would wait ten seconds and then kill.
  std::signal(SIGTERM, stopNow);
  std::signal(SIGINT, stopNow);
  const int server = listenOn(bindAddr, port);
  if (server < 0) {
    std::cerr << "could not listen on " << bindAddr << ":" << port << ": " << std::strerror(errno) << "\n";
    return 1;
  }
  std::cerr << "edge-node listening on " << bindAddr << ":" << port << std::endl;
  std::vector<Client> clients;
  std::vector<pollfd> watch;
  for (;;) {
    watch.assign(1, pollfd{server, POLLIN, 0});
    for (const Client& c : clients) watch.push_back(pollfd{c.fd, POLLIN, 0});
    if (::poll(watch.data(), watch.size(), -1) < 0) continue;  // interrupted: look again
    // Clients first, in the order they connected; `watch[i + 1]` is `clients[i]`.
    std::vector<Client> still;
    for (size_t i = 0; i < clients.size(); ++i) {
      const bool ready = watch[i + 1].revents & (POLLIN | POLLHUP | POLLERR);
      if (!ready || serveReady(clients[i], cfg)) still.push_back(std::move(clients[i]));
      else ::close(clients[i].fd);
    }
    clients.swap(still);
    if (watch[0].revents & POLLIN) {
      const int client = ::accept(server, nullptr, nullptr);
      if (client < 0) continue;
      if (clients.size() >= kMaxClients) { ::close(client); continue; }
      int yes = 1;
      ::setsockopt(client, IPPROTO_TCP, TCP_NODELAY, &yes, sizeof(yes));
      setTimeouts(client, 2);
      clients.push_back(Client{client, std::string()});
    }
  }
}
