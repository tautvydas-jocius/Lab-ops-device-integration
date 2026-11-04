#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <mbedtls/md.h>
#include <HTTPClient.h>
#if __has_include(<esp_eap_client.h>)
#include <esp_eap_client.h>  // Arduino-ESP32 3.x / IDF 5
#define EAP_SET(id, user, pass) do { esp_eap_client_set_identity((const uint8_t *)id, strlen(id)); esp_eap_client_set_username((const uint8_t *)user, strlen(user)); esp_eap_client_set_password((const uint8_t *)pass, strlen(pass)); esp_wifi_sta_enterprise_enable(); } while (0)
#else
#include <esp_wpa2.h>  // Arduino-ESP32 2.x
#define EAP_SET(id, user, pass) do { esp_wifi_sta_wpa2_ent_set_identity((uint8_t *)id, strlen(id)); esp_wifi_sta_wpa2_ent_set_username((uint8_t *)user, strlen(user)); esp_wifi_sta_wpa2_ent_set_password((uint8_t *)pass, strlen(pass)); esp_wifi_sta_wpa2_ent_enable(); } while (0)
#endif
#ifdef USB_MSC_UPLOAD
#include "usb_drop.h"
#endif
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
String wifiSsid = WIFI_SSID, wifiPass = WIFI_PASS, wifiUser, wifiIdent;  // user set => WPA2-Enterprise (PEAP/MSCHAPv2)  // NVS namespace "wifi" overrides secrets.h defaults
bool wifiTrial = false;  // booted on pending creds; revert unless MQTT connects in time
uint32_t bootMs = 0;
const uint32_t TRIAL_MS = 45000;
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
void abandonTrial() {
  prefs.begin("wifi", false); prefs.remove("nssid"); prefs.remove("npass"); prefs.remove("nuser"); prefs.remove("nident"); prefs.end(); delay(100); ESP.restart();
}
void clearConfig() {  // keep cmdid so old signed commands stay unreplayable
  prefs.begin("scale", false); uint32_t keep = prefs.getUInt("cmdid", 0); prefs.clear(); prefs.putUInt("cmdid", keep); prefs.end();
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
  else if (action == "set_wifi") {
    JsonDocument w; String ns, np;
    if (deserializeJson(w, arg) || !(ns = String((const char *)(w["ssid"] | ""))).length() || ns.length() > 32) { ack(id, false, "arg must be {\"ssid\":..,\"pass\":..[,\"eap_user\":..,\"eap_identity\":..]}"); return; }
    np = String((const char *)(w["pass"] | ""));
    prefs.begin("wifi", false); prefs.putString("nssid", ns); prefs.putString("npass", np);
    prefs.putString("nuser", String((const char *)(w["eap_user"] | ""))); prefs.putString("nident", String((const char *)(w["eap_identity"] | ""))); prefs.end();  // eap_user => WPA2-Enterprise, "pass" is then the EAP password
    ack(id, true, "stored pending creds, rebooting; reverts in " + String(TRIAL_MS / 1000) + "s if MQTT does not connect"); mqtt.loop(); delay(200); ESP.restart();
  }
  else if (action == "reset_config") { clearConfig(); ack(id, true, "device config cleared, rebooting (retained config will be re-applied)"); mqtt.loop(); delay(200); ESP.restart(); }
  else if (action == "factory_reset") {
    clearConfig(); prefs.begin("wifi", false); prefs.clear(); prefs.end();
    ack(id, true, "factory reset: config + stored Wi-Fi cleared, rebooting on secrets.h Wi-Fi"); mqtt.loop(); delay(200); ESP.restart();
  }
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
// Wi-Fi creds: "ssid"/"pass" = active, "nssid"/"npass" = pending (set_wifi). Pending is trialled once and only
// promoted after MQTT connects; if the trial fails the MCU reverts and reboots, so a typo can't strand the device.
void loadWifi() {
  prefs.begin("wifi", false);
  const char *k = prefs.getString("nssid", "").length() ? "n" : "";  // pending keys are prefixed "n"
  if (k[0] || prefs.getString("ssid", "").length()) {
    wifiSsid = prefs.getString((String(k) + "ssid").c_str(), ""); wifiPass = prefs.getString((String(k) + "pass").c_str(), "");
    wifiUser = prefs.getString((String(k) + "user").c_str(), ""); wifiIdent = prefs.getString((String(k) + "ident").c_str(), "");
    wifiTrial = k[0] == 'n';
  }
  prefs.end();
}
void commitWifi() {
  prefs.begin("wifi", false); prefs.putString("ssid", wifiSsid); prefs.putString("pass", wifiPass); prefs.putString("user", wifiUser); prefs.putString("ident", wifiIdent);
  prefs.remove("nssid"); prefs.remove("npass"); prefs.remove("nuser"); prefs.remove("nident"); prefs.end(); wifiTrial = false;
}
void beginWifi() {
  WiFi.mode(WIFI_STA);
  if (wifiUser.length()) { EAP_SET(wifiIdent.length() ? wifiIdent.c_str() : wifiUser.c_str(), wifiUser.c_str(), wifiPass.c_str()); WiFi.begin(wifiSsid.c_str()); }
  else WiFi.begin(wifiSsid.c_str(), wifiPass.c_str());
}
void connectWifi() {
  if (WiFi.status() == WL_CONNECTED) return; beginWifi();
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    if (wifiTrial && millis() - bootMs > TRIAL_MS) abandonTrial();
  }
}
void connectMqtt() {
  while (!mqtt.connected()) {
    String will = "{\"state\":\"offline\",\"config_version\":" + String(configVersion) + ",\"fw\":\"" FW_VERSION "\"}";
    if (mqtt.connect(DEVICE_ID, statusTopic.c_str(), 1, true, will.c_str())) { mqtt.subscribe(configTopic.c_str()); mqtt.subscribe(cmdTopic.c_str(), 1); if (wifiTrial) commitWifi(); publishStatus("online"); }
    else if (wifiTrial && millis() - bootMs > TRIAL_MS) abandonTrial();
    else delay(2000);
  }
}
void setup() {
  Serial.begin(115200); rawTopic = String(PREFIX) + "/" DEVICE_ID "/raw"; configTopic = String(PREFIX) + "/" DEVICE_ID "/config"; statusTopic = String(PREFIX) + "/" DEVICE_ID "/status"; cmdTopic = String(PREFIX) + "/" DEVICE_ID "/cmd"; ackTopic = String(PREFIX) + "/" DEVICE_ID "/ack";
  prefs.begin("scale", true); lastCmdId = prefs.getUInt("cmdid", 0); prefs.end();
  bootMs = millis(); loadWifi();
  loadConfig();
#ifdef USB_MSC_UPLOAD
  usbDropBegin();
#endif
  beginWifi(); mqtt.setServer(MQTT_HOST, MQTT_PORT); mqtt.setCallback(onMessage); mqtt.setBufferSize(512);
}
void loop() {
  if (WiFi.status() != WL_CONNECTED) connectWifi();
  if (!mqtt.connected()) connectMqtt();
  mqtt.loop();
#ifdef USB_MSC_UPLOAD
  usbDropPoll();
#endif
  if (enabled && pollCommand.length() && millis() - lastPoll >= intervalMs) { Serial1.print(pollCommand); lastPoll = millis(); }
  while (Serial1.available()) {
    char c = (char)Serial1.read(); lineBuffer += c;
    if (lineBuffer.endsWith(terminator)) {
      JsonDocument doc; doc["line"] = lineBuffer; doc["seq"] = ++seqNo; doc["uptime_ms"] = millis();
      String payload; serializeJson(doc, payload); if (enabled) mqtt.publish(rawTopic.c_str(), payload.c_str()); lineBuffer = "";
    } else if (lineBuffer.length() > 256) lineBuffer = "";
  }
}
