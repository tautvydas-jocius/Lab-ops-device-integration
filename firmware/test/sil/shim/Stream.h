#pragma once
#include <cstddef>
#include <cstdint>
class Print { public: virtual ~Print() {} virtual size_t write(uint8_t) = 0; virtual size_t write(const uint8_t *b, size_t n) { size_t k = 0; while (n--) k += write(*b++); return k; } };
class Stream : public Print { public: virtual int available() = 0; virtual int read() = 0; virtual int peek() = 0; virtual void flush() {} };
