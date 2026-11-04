#pragma once
#include "Arduino.h"
class UpdateClass { public: bool begin(size_t n); size_t write(uint8_t *b, size_t n); bool end(bool even = false); void abort(); const char *errorString(); };
extern UpdateClass Update;
