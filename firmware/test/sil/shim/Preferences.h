#pragma once
#include "Arduino.h"
#include <map>
#include <string>
class Preferences {
 public:
  bool begin(const char *ns, bool ro = false);
  void end();
  bool clear();
  bool remove(const char *k);
  uint32_t getUInt(const char *k, uint32_t d = 0);
  size_t putUInt(const char *k, uint32_t v);
  uint8_t getUChar(const char *k, uint8_t d = 0);
  size_t putUChar(const char *k, uint8_t v);
  int8_t getChar(const char *k, int8_t d = 0);
  size_t putChar(const char *k, int8_t v);
  bool getBool(const char *k, bool d = false);
  size_t putBool(const char *k, bool v);
  String getString(const char *k, const String &d = String());
  size_t putString(const char *k, const String &v);
 private:
  bool get(const char *k, std::string &out);
  size_t put(const char *k, const std::string &v);
  void flush();
  bool started = false, ro_ = false; std::string ns_; std::map<std::string, std::string> m;
};
