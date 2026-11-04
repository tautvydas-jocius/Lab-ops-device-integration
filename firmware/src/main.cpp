#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <mbedtls/md.h>
#include "secrets.h"

#define UART_RX 18
#define UART_TX 17
#define FW_VERSION "1.0.0"
static const char *PREFIX = "lab/devices";
WiFiClient network;
PubSubClient mqtt(network);
Preferences prefs;
String rawTopic, configTopic, statusTopic, cmdTopic, ackTopic;
uint32_t lastCmdId = 0;
uint32_t configVersion = 0, baud = 9600, intervalMs = 1000, lastPoll = 0, seqNo = 0;
uint8_t dataBits = 8, stopBits = 1;
char parity = 'N';
String terminator = "\r\n", pollCommand;
bool enabled = true;
String lineBuffer;

uint32_t serialConfig() {
  if (dataBits == 7) {
    if (stopBits == 2) return parity == 'E' ? SERIAL_7E2 : (parity == 'O' ? SERIAL_7O2 : SERIAL_7N2);
    return parity == 'E' ? SERIAL_7E1 : (parity == 'O' ? SERIAL_7O1 : SERIAL_7N1);
  }
  if (stopBits == 2) return parity == 'E' ? SERIAL_8E2 : (parity == 'O' ? SERIAL_8O2 : SERIAL_8N2);
  return parity == 'E' ? SERIAL_8E1 : (parity == 'O' ? SERIAL_8O1 : SERIAL_8N1);
}
void applySerial() { Serial1.end(); Serial1.begin(baud, serialConfig(), UART_RX, UART_TX); }
void publishStatus(const char *state) {
  if (!mqtt.connected()) return;
  JsonDocument doc; doc["state"] = state; doc["config_version"] = configVersion; doc["fw"] = FW_VERSION;
  String payload; serializeJson(doc, payload); mqtt.publish(statusTopic.c_str(), payload.c_str(), true);
}
void saveConfig() {
  prefs.begin("scale", false); prefs.putUInt("version", configVersion); prefs.putUInt("baud", baud);
  prefs.putUInt("interval", intervalMs); prefs.putUChar("bits", dataBits); prefs.putUChar("stops", stopBits);
  prefs.putChar("parity", parity); prefs.putString("term", terminator); prefs.putString("poll", pollCommand); prefs.putBool("enabled", enabled); prefs.end();
}
void loadConfig() {
  prefs.begin("scale", true); configVersion = prefs.getUInt("version", 0); baud = prefs.getUInt("baud", 9600);
  intervalMs = prefs.getUInt("interval", 1000); dataBits = prefs.getUChar("bits", 8); stopBits = prefs.getUChar("stops", 1);
  parity = prefs.getChar("parity", 'N'); terminator = prefs.getString("term", "\r\n"); pollCommand = prefs.getString("poll", ""); enabled = prefs.getBool("enabled", true); prefs.end();
  applySerial();
}
// HMAC-SHA256(COMMAND_TOKEN, "id|action|arg") as lowercase hex; matches server mqtt.sign()
String hmacHex(const String &msg) {
  uint8_t out[32]; mbedtls_md_hmac(mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), (const uint8_t *)COMMAND_TOKEN, strlen(COMMAND_TOKEN), (const uint8_t *)msg.c_str(), msg.length(), out);
  String hex; char b[3]; for (int i = 0; i < 32; i++) { snprintf(b, 3, "%02x", out[i]); hex += b; } return hex;
}
bool sameString(const String &a, const String &b) {  // constant-time
  if (a.length() != b.length()) return false; uint8_t d = 0; for (size_t i = 0; i < a.length(); i++) d |= a[i] ^ b[i]; return d == 0;
}
void ack(uint32_t id, bool ok, const String &result) {
  JsonDocument doc; doc["id"] = id; doc["ok"] = ok; doc["result"] = result;
  String payload; serializeJson(doc, payload); mqtt.publish(ackTopic.c_str(), payload.c_str());
}
void onCommand(byte *payload, unsigned int length) {
  JsonDocument doc; if (deserializeJson(doc, payload, length)) return;
  uint32_t id = doc["id"] | 0; String action = doc["action"] | "", arg = doc["arg"] | "", sig = doc["sig"] | "";
  if (!sameString(hmacHex(String(id) + "|" + action + "|" + arg), sig)) return;  // bad token: drop silently, no oracle
  if (id <= lastCmdId) { ack(id, false, "replayed or stale id"); return; }
  lastCmdId = id; prefs.begin("scale", false); prefs.putUInt("cmdid", id); prefs.end();
  if (action == "ping") ack(id, true, String("pong fw=" FW_VERSION " up=") + millis() / 1000 + "s rssi=" + WiFi.RSSI());
  else if (action == "poll_now") { if (pollCommand.length()) { Serial1.print(pollCommand); ack(id, true, "polled"); } else ack(id, false, "no poll_command configured"); }
  else if (action == "send_serial") { Serial1.print(arg); ack(id, true, "sent " + String(arg.length()) + " bytes"); }
  else if (action == "reboot") { ack(id, true, "rebooting"); mqtt.loop(); delay(200); ESP.restart(); }
  else ack(id, false, "unknown action");
}
void onMessage(char *topic, byte *payload, unsigned int length) {
  String t(topic); if (t == cmdTopic) { onCommand(payload, length); return; } if (t != configTopic) return;
  JsonDocument doc; if (deserializeJson(doc, payload, length)) return;
  configVersion = doc["config_version"] | configVersion; enabled = doc["enabled"] | enabled;
  baud = doc["baud"] | baud; dataBits = doc["data_bits"] | dataBits; stopBits = doc["stop_bits"] | stopBits;
  const char *p = doc["parity"] | "N"; parity = p[0]; terminator = String((const char *)(doc["terminator"] | "\r\n"));
  pollCommand = String((const char *)(doc["poll_command"] | "")); intervalMs = doc["poll_interval_ms"] | intervalMs;
  applySerial(); saveConfig(); publishStatus("online");
}
void connectWifi() { if (WiFi.status() == WL_CONNECTED) return; WiFi.mode(WIFI_STA); WiFi.begin(WIFI_SSID, WIFI_PASS); while (WiFi.status() != WL_CONNECTED) delay(500); }
void connectMqtt() {
  while (!mqtt.connected()) {
    String will = "{\"state\":\"offline\",\"config_version\":" + String(configVersion) + ",\"fw\":\"" FW_VERSION "\"}";
    if (mqtt.connect(DEVICE_ID, statusTopic.c_str(), 1, true, will.c_str())) { mqtt.subscribe(configTopic.c_str()); mqtt.subscribe(cmdTopic.c_str(), 1); publishStatus("online"); }
    else delay(2000);
  }
}
void setup() {
  Serial.begin(115200); rawTopic = String(PREFIX) + "/" DEVICE_ID "/raw"; configTopic = String(PREFIX) + "/" DEVICE_ID "/config"; statusTopic = String(PREFIX) + "/" DEVICE_ID "/status"; cmdTopic = String(PREFIX) + "/" DEVICE_ID "/cmd"; ackTopic = String(PREFIX) + "/" DEVICE_ID "/ack";
  prefs.begin("scale", true); lastCmdId = prefs.getUInt("cmdid", 0); prefs.end();
  loadConfig(); WiFi.mode(WIFI_STA); WiFi.begin(WIFI_SSID, WIFI_PASS); mqtt.setServer(MQTT_HOST, MQTT_PORT); mqtt.setCallback(onMessage); mqtt.setBufferSize(512);
}
void loop() {
  if (WiFi.status() != WL_CONNECTED) connectWifi();
  if (!mqtt.connected()) connectMqtt();
  mqtt.loop();
  if (enabled && pollCommand.length() && millis() - lastPoll >= intervalMs) { Serial1.print(pollCommand); lastPoll = millis(); }
  while (Serial1.available()) {
    char c = (char)Serial1.read(); lineBuffer += c;
    if (lineBuffer.endsWith(terminator)) {
      JsonDocument doc; doc["line"] = lineBuffer; doc["seq"] = ++seqNo; doc["uptime_ms"] = millis();
      String payload; serializeJson(doc, payload); if (enabled) mqtt.publish(rawTopic.c_str(), payload.c_str()); lineBuffer = "";
    } else if (lineBuffer.length() > 256) lineBuffer = "";
  }
}
