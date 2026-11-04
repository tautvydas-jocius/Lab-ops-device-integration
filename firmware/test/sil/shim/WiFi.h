#pragma once
#include "Arduino.h"
#include "Client.h"
enum { WL_IDLE_STATUS = 0, WL_CONNECTED = 3, WL_DISCONNECTED = 6 };
enum { WIFI_STA = 1 };
typedef enum { ARDUINO_EVENT_WIFI_STA_DISCONNECTED = 5, ARDUINO_EVENT_WIFI_STA_GOT_IP = 7 } WiFiEvent_t;
struct WiFiEventInfo_t { struct { uint8_t reason; } wifi_sta_disconnected; };
typedef void (*WiFiEventSysCb)(WiFiEvent_t, WiFiEventInfo_t);
class WiFiClass {
 public:
  void mode(int) {}
  void begin(const char *ssid, const char *pass = nullptr);
  int status();
  int RSSI();
  int channel() { return 6; }
  void onEvent(WiFiEventSysCb cb);
};
extern WiFiClass WiFi;
class WiFiClient : public Client {
 public:
  int connect(IPAddress ip, uint16_t port) override;
  int connect(const char *host, uint16_t port) override;
  size_t write(uint8_t b) override { return write(&b, 1); }
  size_t write(const uint8_t *b, size_t n) override;
  int available() override;
  int read() override;
  int read(uint8_t *b, size_t n) override;
  int peek() override;
  void flush() override {}
  void stop() override;
  uint8_t connected() override;
  operator bool() override { return fd >= 0; }
  int fd = -1;
};
