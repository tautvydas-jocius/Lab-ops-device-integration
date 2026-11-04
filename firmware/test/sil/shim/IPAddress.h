#pragma once
#include <cstdint>
class IPAddress { public: uint8_t b[4] = {0, 0, 0, 0}; IPAddress() {} IPAddress(uint8_t x, uint8_t y, uint8_t z, uint8_t w) { b[0] = x; b[1] = y; b[2] = z; b[3] = w; } uint8_t operator[](int i) const { return b[i]; } };
