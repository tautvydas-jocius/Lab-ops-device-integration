#pragma once
// Software-in-the-loop Arduino layer: the real firmware sources compile against this on Linux.
// Faithful on purpose where a bug could hide: bounded UART RX buffer, Preferences open/closed + 15-char key rules,
// ESP.restart() really re-execs the process (RAM is lost, "NVS" files survive, sockets close so the broker fires the LWT).
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cctype>
#include <algorithm>
#include "WString.h"
#include "Stream.h"
using std::min; using std::max;
typedef bool boolean;
typedef uint8_t byte;
#define PROGMEM
#define PSTR(s) (s)
#define pgm_read_byte(p) (*(const uint8_t *)(p))
#define pgm_read_byte_near(p) (*(const uint8_t *)(p))
#define strlcpy sil_strlcpy
size_t sil_strlcpy(char *d, const char *s, size_t n);
unsigned long millis();
void delay(unsigned long ms);
inline void yield() {}
uint32_t esp_random();
bool psramFound();
void *ps_malloc(size_t n);
enum { SERIAL_7N1 = 1, SERIAL_7E1, SERIAL_7O1, SERIAL_7N2, SERIAL_7E2, SERIAL_7O2, SERIAL_8N1, SERIAL_8E1, SERIAL_8O1, SERIAL_8N2, SERIAL_8E2, SERIAL_8O2 };
class HardwareSerial {
 public:
  explicit HardwareSerial(int n) : num(n) {}
  void begin(unsigned long baud, uint32_t cfg = SERIAL_8N1, int rx = -1, int tx = -1);
  void end();
  size_t setRxBufferSize(size_t n);
  int available();
  int read();
  size_t print(const String &x);
  size_t print(const char *x) { return print(String(x)); }
  int printf(const char *fmt, ...) __attribute__((format(printf, 2, 3)));
  int num;
};
extern HardwareSerial Serial, Serial1;
typedef enum { ESP_RST_UNKNOWN, ESP_RST_POWERON, ESP_RST_EXT, ESP_RST_SW, ESP_RST_PANIC, ESP_RST_INT_WDT, ESP_RST_TASK_WDT, ESP_RST_WDT, ESP_RST_DEEPSLEEP, ESP_RST_BROWNOUT, ESP_RST_SDIO } esp_reset_reason_t;
esp_reset_reason_t esp_reset_reason();
class EspClass { public: [[noreturn]] void restart(); uint32_t getFreeHeap(); uint32_t getMinFreeHeap(); };
extern EspClass ESP;
void setup();
void loop();
