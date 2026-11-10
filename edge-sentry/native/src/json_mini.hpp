#pragma once
// Tiny helpers for a fixed JSON dialect used by edge-sim.
// Not a general-purpose parser — keep the world schema small.

#include <cctype>
#include <sstream>
#include <string>
#include <vector>

inline std::string jsonEscape(const std::string& s) {
  std::ostringstream o;
  for (char c : s) {
    switch (c) {
      case '"': o << "\\\""; break;
      case '\\': o << "\\\\"; break;
      case '\n': o << "\\n"; break;
      default: o << c; break;
    }
  }
  return o.str();
}

inline std::string jsonString(const std::string& s) {
  return "\"" + jsonEscape(s) + "\"";
}

inline std::string joinComma(const std::vector<std::string>& parts) {
  std::ostringstream o;
  for (size_t i = 0; i < parts.size(); ++i) {
    if (i) o << ",";
    o << parts[i];
  }
  return o.str();
}

// Very small extractors for our hand-written world files.
inline bool extractStringField(const std::string& obj, const std::string& key, std::string& out) {
  const std::string needle = "\"" + key + "\"";
  auto pos = obj.find(needle);
  if (pos == std::string::npos) return false;
  pos = obj.find(':', pos);
  if (pos == std::string::npos) return false;
  pos = obj.find('"', pos);
  if (pos == std::string::npos) return false;
  auto end = obj.find('"', pos + 1);
  if (end == std::string::npos) return false;
  out = obj.substr(pos + 1, end - pos - 1);
  return true;
}

inline bool extractNumberField(const std::string& obj, const std::string& key, double& out) {
  const std::string needle = "\"" + key + "\"";
  auto pos = obj.find(needle);
  if (pos == std::string::npos) return false;
  pos = obj.find(':', pos);
  if (pos == std::string::npos) return false;
  ++pos;
  while (pos < obj.size() && std::isspace(static_cast<unsigned char>(obj[pos]))) ++pos;
  try {
    size_t consumed = 0;
    out = std::stod(obj.substr(pos), &consumed);
    return consumed > 0;
  } catch (...) {
    return false;
  }
}

inline bool extractIntField(const std::string& obj, const std::string& key, int& out) {
  double d = 0;
  if (!extractNumberField(obj, key, d)) return false;
  out = static_cast<int>(d);
  return true;
}

// Split top-level {...} objects inside a JSON array string.
inline std::vector<std::string> splitObjects(const std::string& arrayBody) {
  std::vector<std::string> objs;
  int depth = 0;
  size_t start = std::string::npos;
  for (size_t i = 0; i < arrayBody.size(); ++i) {
    char c = arrayBody[i];
    if (c == '{') {
      if (depth == 0) start = i;
      ++depth;
    } else if (c == '}') {
      --depth;
      if (depth == 0 && start != std::string::npos) {
        objs.push_back(arrayBody.substr(start, i - start + 1));
        start = std::string::npos;
      }
    }
  }
  return objs;
}

inline std::string extractArrayBody(const std::string& json, const std::string& key) {
  const std::string needle = "\"" + key + "\"";
  auto pos = json.find(needle);
  if (pos == std::string::npos) return {};
  pos = json.find('[', pos);
  if (pos == std::string::npos) return {};
  int depth = 0;
  for (size_t i = pos; i < json.size(); ++i) {
    if (json[i] == '[') ++depth;
    else if (json[i] == ']') {
      --depth;
      if (depth == 0) return json.substr(pos + 1, i - pos - 1);
    }
  }
  return {};
}