#pragma once
#define WIFI_SSID "your-wifi"
#define WIFI_PASS "your-password"
#define MQTT_HOST "192.168.1.10"
#define MQTT_PORT 1883
#define DEVICE_ID "bal-01"
// Per-device secret from Django admin (Device -> command token). Commands are HMAC-signed with it.
#define COMMAND_TOKEN "paste-token-from-admin"
// Comment out to disable the USB flash-drive upload mode (needs PSRAM for the full 4 MB drive)
#define USB_MSC_UPLOAD
