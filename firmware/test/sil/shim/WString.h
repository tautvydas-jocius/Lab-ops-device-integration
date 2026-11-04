#pragma once
// Arduino String on std::string, enough for the firmware, PubSubClient and ArduinoJson.
#include <string>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <algorithm>
#include <type_traits>
#define HEX 16
class String {
 public:
  std::string s;
  String() {}
  String(const char *c) : s(c ? c : "") {}
  String(const std::string &x) : s(x) {}
  String(char c) : s(1, c) {}
  String(int v) : s(std::to_string(v)) {}
  String(long v) : s(std::to_string(v)) {}
  String(unsigned long v) : s(std::to_string(v)) {}
  String(unsigned v, unsigned char base = 10) { if (base == 16) { char b[16]; snprintf(b, sizeof b, "%x", v); s = b; } else s = std::to_string(v); }
  String(float v, unsigned char d = 2) { char b[64]; snprintf(b, sizeof b, "%.*f", d, v); s = b; }
  String(double v, unsigned char d = 2) { char b[64]; snprintf(b, sizeof b, "%.*f", d, v); s = b; }
  const char *c_str() const { return s.c_str(); }
  unsigned length() const { return s.size(); }
  bool concat(const char *c) { s += c; return true; }
  bool concat(const char *c, unsigned n) { s.append(c, n); return true; }
  bool reserve(unsigned n) { s.reserve(n); return true; }
  char operator[](unsigned i) const { return i < s.size() ? s[i] : 0; }
  String &operator+=(const String &o) { s += o.s; return *this; }
  String &operator+=(const char *o) { s += o; return *this; }
  String &operator+=(char o) { s += o; return *this; }
  bool operator==(const String &o) const { return s == o.s; }
  bool operator==(const char *o) const { return s == (o ? o : ""); }
  bool operator!=(const String &o) const { return s != o.s; }
  bool operator!=(const char *o) const { return s != (o ? o : ""); }
  bool endsWith(const String &o) const { return s.size() >= o.s.size() && s.compare(s.size() - o.s.size(), o.s.size(), o.s) == 0; }
  bool startsWith(const String &o) const { return s.compare(0, o.s.size(), o.s) == 0; }
  int lastIndexOf(char c) const { auto p = s.rfind(c); return p == std::string::npos ? -1 : (int)p; }
  String substring(unsigned a) const { return a >= s.size() ? String() : String(s.substr(a)); }
  String substring(unsigned a, unsigned b) const { if (a >= s.size()) return String(); return String(s.substr(a, b > a ? b - a : 0)); }
  long toInt() const { return atol(s.c_str()); }
  void toLowerCase() { std::transform(s.begin(), s.end(), s.begin(), ::tolower); }
};
inline String operator+(const String &a, const String &b) { return String(a.s + b.s); }
inline String operator+(const String &a, const char *b) { return String(a.s + b); }
inline String operator+(const char *a, const String &b) { return String(std::string(a) + b.s); }
template <class T, class = typename std::enable_if<std::is_arithmetic<T>::value>::type>
inline String operator+(const String &a, T b) { return a + String(b); }
