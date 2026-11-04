#pragma once
#include "Arduino.h"
typedef int32_t (*msc_read_cb)(uint32_t lba, uint32_t off, void *buf, uint32_t n);
typedef int32_t (*msc_write_cb)(uint32_t lba, uint32_t off, uint8_t *buf, uint32_t n);
class USBMSC {
 public:
  void vendorID(const char *) {} void productID(const char *) {} void productRevision(const char *) {}
  void onRead(msc_read_cb c) { rd = c; } void onWrite(msc_write_cb c) { wr = c; } void mediaPresent(bool) {}
  bool begin(uint32_t sectors, uint16_t size);
  msc_read_cb rd = nullptr; msc_write_cb wr = nullptr; uint32_t sectors = 0; uint16_t size = 0;
};
