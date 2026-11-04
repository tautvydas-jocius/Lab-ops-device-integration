#pragma once
// USB "drop drive": the ESP32-S3 enumerates as a small FAT12 flash drive backed by RAM. When the host has been
// quiet for DROP_IDLE_MS, the root directory is scanned and every new/changed .csv/.pdf/.txt is POSTed to Django
// (Bearer COMMAND_TOKEN). Nothing is persisted: the drive is empty after a reboot. The host is never modified.
#include <Arduino.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include "USB.h"
#include "USBMSC.h"
#include "secrets.h"

#define SECTOR 512
#define DROP_IDLE_MS 2000
#define MAX_FILE (4u * 1024 * 1024)

static USBMSC msc;
static uint8_t *disk = nullptr;
static uint32_t totalSectors, spc, fatSectors, rootSectors = 32, dataStart, nClusters;
static volatile uint32_t lastWriteMs = 0;
static volatile bool dirty = false;
struct Sent { char name[64]; uint32_t size, crc; };
static Sent sent[24]; static int nSent = 0;

static void put16(uint8_t *p, uint16_t v) { p[0] = v; p[1] = v >> 8; }
static void put32(uint8_t *p, uint32_t v) { put16(p, v); put16(p + 2, v >> 16); }
static uint32_t crc32(const uint8_t *d, size_t n, uint32_t c = 0xFFFFFFFF) {
  while (n--) { c ^= *d++; for (int k = 0; k < 8; k++) c = (c >> 1) ^ (0xEDB88320u & -(c & 1)); } return ~c;
}
static void formatDisk() {  // FAT12, 2 FATs, 512 root entries; cluster size picked so clusters <= 4084
  memset(disk, 0, totalSectors * SECTOR);
  nClusters = 0; spc = 1;
  while ((totalSectors / spc) > 4000) spc *= 2;
  uint32_t clusters = totalSectors / spc; fatSectors = ((clusters + 2) * 3 / 2 + SECTOR - 1) / SECTOR;
  dataStart = 1 + 2 * fatSectors + rootSectors; nClusters = (totalSectors - dataStart) / spc;
  uint8_t *b = disk;
  b[0] = 0xEB; b[1] = 0x3C; b[2] = 0x90; memcpy(b + 3, "LABOPS  ", 8);
  put16(b + 11, SECTOR); b[13] = spc; put16(b + 14, 1); b[16] = 2; put16(b + 17, 512);
  put16(b + 19, totalSectors); b[21] = 0xF8; put16(b + 22, fatSectors); put16(b + 24, 32); put16(b + 26, 64);
  b[36] = 0x80; b[38] = 0x29; put32(b + 39, 0x4C414253); memcpy(b + 43, "LABOPS DROP", 11); memcpy(b + 54, "FAT12   ", 8);
  b[510] = 0x55; b[511] = 0xAA;
  for (int f = 0; f < 2; f++) { uint8_t *t = disk + (1 + f * fatSectors) * SECTOR; t[0] = 0xF8; t[1] = 0xFF; t[2] = 0xFF; }
}
static int32_t onRead(uint32_t lba, uint32_t off, void *buf, uint32_t n) {
  if (lba >= totalSectors) return -1; memcpy(buf, disk + lba * SECTOR + off, n); return n;
}
static int32_t onWrite(uint32_t lba, uint32_t off, uint8_t *buf, uint32_t n) {
  if (lba >= totalSectors) return -1; memcpy(disk + lba * SECTOR + off, buf, n); lastWriteMs = millis(); dirty = true; return n;
}
static uint16_t fatNext(uint16_t c) {
  const uint8_t *t = disk + SECTOR; uint32_t o = c * 3 / 2; uint16_t v = t[o] | (t[o + 1] << 8);
  return (c & 1) ? v >> 4 : v & 0x0FFF;
}
// Reads a file's cluster chain into a heap buffer (nullptr on corrupt/oversized chain).
static uint8_t *readChain(uint16_t first, uint32_t size) {
  if (!size || size > MAX_FILE || first < 2) return nullptr;
  uint8_t *out = (uint8_t *)(psramFound() ? ps_malloc(size) : malloc(size)); if (!out) return nullptr;
  uint32_t got = 0, csz = spc * SECTOR; uint16_t c = first;
  while (got < size) {
    if (c < 2 || c >= nClusters + 2) { free(out); return nullptr; }
    uint32_t n = min(csz, size - got); memcpy(out + got, disk + (dataStart + (c - 2) * spc) * SECTOR, n); got += n;
    c = fatNext(c); if (c >= 0xFF8 && got < size) { free(out); return nullptr; }
  }
  return out;
}
static bool allowedName(const String &n) {
  String l = n; l.toLowerCase(); return l.endsWith(".csv") || l.endsWith(".pdf") || l.endsWith(".txt");
}
static bool alreadySent(const String &n, uint32_t size, uint32_t crc) {
  for (int i = 0; i < nSent; i++) if (n == sent[i].name) return sent[i].size == size && sent[i].crc == crc; return false;
}
static void markSent(const String &n, uint32_t size, uint32_t crc) {
  int i = 0; while (i < nSent && n != sent[i].name) i++;
  if (i == nSent) { if (nSent == 24) i = --nSent; else nSent++; }  // full: overwrite last slot
  strlcpy(sent[i].name, n.c_str(), sizeof(sent[i].name)); sent[i].size = size; sent[i].crc = crc;
}
static String urlEncode(const String &s) {
  String o; char h[4]; for (char c : s) { if (isalnum(c) || c == '.' || c == '-' || c == '_') o += c; else { snprintf(h, 4, "%%%02X", (uint8_t)c); o += h; } } return o;
}
static bool upload(const String &name, const uint8_t *data, uint32_t size) {
  HTTPClient http; String url = String(UPLOAD_BASE_URL) + "/api/devices/" DEVICE_ID "/files/?name=" + urlEncode(name);
  if (!http.begin(url)) return false;
  http.addHeader("Authorization", String("Bearer ") + COMMAND_TOKEN); http.addHeader("Content-Type", "application/octet-stream"); http.setTimeout(20000);
  int code = http.POST((uint8_t *)data, size); http.end(); return code == 201 || code == 200;
}
static void scanRoot() {
  const uint8_t *root = disk + (1 + 2 * fatSectors) * SECTOR; char lfn[256]; bool haveLfn = false; memset(lfn, 0, sizeof(lfn));
  for (int i = 0; i < 512; i++) {
    const uint8_t *e = root + i * 32;
    if (e[0] == 0x00) break; if (e[0] == 0xE5) { haveLfn = false; continue; }
    if (e[11] == 0x0F) {  // long-name piece: 13 UTF-16 chars, ASCII low bytes kept
      int seq = (e[0] & 0x1F) - 1; if (e[0] & 0x40) { memset(lfn, 0, sizeof(lfn)); haveLfn = true; }
      static const uint8_t pos[13] = {1, 3, 5, 7, 9, 14, 16, 18, 20, 22, 24, 28, 30};
      if (seq >= 0 && seq < 19) for (int k = 0; k < 13; k++) { uint8_t ch = e[pos[k]]; if (ch && ch != 0xFF) lfn[seq * 13 + k] = ch; }
      continue;
    }
    if (e[11] & 0x18) { haveLfn = false; continue; }  // volume label or directory
    String name;
    if (haveLfn && lfn[0]) name = lfn;
    else { for (int k = 0; k < 8 && e[k] != ' '; k++) name += (char)e[k]; String ext; for (int k = 8; k < 11 && e[k] != ' '; k++) ext += (char)e[k]; if (ext.length()) name += "." + ext; }
    haveLfn = false; memset(lfn, 0, sizeof(lfn));
    uint32_t size = e[28] | (e[29] << 8) | (e[30] << 16) | ((uint32_t)e[31] << 24); uint16_t first = e[26] | (e[27] << 8);
    if (!allowedName(name) || !size) continue;
    uint8_t *data = readChain(first, size); if (!data) continue;
    uint32_t crc = crc32(data, size);
    if (!alreadySent(name, size, crc) && WiFi.status() == WL_CONNECTED) {
      if (upload(name, data, size)) markSent(name, size, crc);
      else { Serial.printf("drop upload failed: %s\n", name.c_str()); dirty = true; lastWriteMs = millis(); }  // retry after another idle window
    }
    free(data);
  }
}
static void usbDropBegin() {
  size_t bytes = 4u * 1024 * 1024; disk = psramFound() ? (uint8_t *)ps_malloc(bytes) : nullptr;
  if (!disk) { bytes = 256u * 1024; disk = (uint8_t *)malloc(bytes); }  // no PSRAM: tiny drive
  if (!disk) return; totalSectors = bytes / SECTOR; formatDisk();
  msc.vendorID("LabOps"); msc.productID("Drop Drive"); msc.productRevision("1.0"); msc.onRead(onRead); msc.onWrite(onWrite);
  msc.mediaPresent(true); msc.begin(totalSectors, SECTOR); USB.begin();
}
static void usbDropPoll() {
  if (dirty && millis() - lastWriteMs > DROP_IDLE_MS) { dirty = false; scanRoot(); }
}
