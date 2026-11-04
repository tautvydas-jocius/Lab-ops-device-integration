#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <mbedtls/md.h>
#include <Update.h>
#if __has_include(<esp_eap_client.h>)
#include <esp_eap_client.h>  // Arduino-ESP32 3.x / IDF 5
#define EAP_SET(id, user, pass) do { esp_eap_client_set_identity((const uint8_t *)(id), strlen(id)); esp_eap_client_set_username((const uint8_t *)(user), strlen(user)); esp_eap_client_set_password((const uint8_t *)(pass), strlen(pass)); esp_wifi_sta_enterprise_enable(); } while (0)
#else
#include <esp_wpa2.h>  // Arduino-ESP32 2.x
#define EAP_SET(id, user, pass) do { esp_wifi_sta_wpa2_ent_set_identity((uint8_t *)(id), strlen(id)); esp_wifi_sta_wpa2_ent_set_username((uint8_t *)(user), strlen(user)); esp_wifi_sta_wpa2_ent_set_password((uint8_t *)(pass), strlen(pass)); esp_wifi_sta_wpa2_ent_enable(); } while (0)
#endif
#include "secrets.h"

#ifndef UART_RX_PIN
#define UART_RX_PIN 18  // devkit default; ATOM S3 Lite env overrides (Grove G1)
#endif
#ifndef UART_TX_PIN
#define UART_TX_PIN 17  // ATOM S3 Lite: Grove G2
#endif
#define UART_RX UART_RX_PIN
#define UART_TX UART_TX_PIN
#ifndef FW_VERSION
#define FW_VERSION "1.0.0"  // bump (or -DFW_VERSION=\"x\") per build; Django shows it per device
#endif
// Embedded so Django can read the version straight out of firmware.bin when it is uploaded (auto-versioning)
extern const char FW_MARKER[] __attribute__((used)) = "LABOPS_FW=" FW_VERSION;
static const char *PREFIX = "lab/devices";
WiFiClient network;
PubSubClient mqtt(network);
Preferences prefs;
String rawTopic, configTopic, statusTopic, cmdTopic, ackTopic;
uint32_t lastCmdId = 0;
String fileBase, fileAckTopic, otaReqTopic, otaBase;
// OTA: onCommand only records the request; otaRun() executes from loop() because it needs mqtt.loop() (not re-entrant in a callback)
bool otaPending = false; uint32_t otaCmdId = 0, otaImage = 0, otaSize = 0; String otaVersion, otaSha;
uint8_t otaBuf[4096]; volatile int otaLen = -1; volatile uint32_t otaIdx = 0;
volatile uint32_t ackXfer = 0; volatile int ackState = 0;  // set by file_ack handler
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
void applySerial() { Serial1.end(); Serial1.setRxBufferSize(2048); Serial1.begin(baud, serialConfig(), UART_RX, UART_TX); }
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
// Poll tick + serial line reader. Called from loop() AND from every long blocking section (file upload, OTA, CRC over a
// 4 MB drive) because the UART FIFO is only drained here: unpumped, a balance streaming 960 B/s overflows the buffer
// in ~2 s and lines are silently lost. Never call from inside an MQTT callback (PubSubClient is not re-entrant).
void pumpSerial() {
  if (enabled && pollCommand.length() && millis() - lastPoll >= intervalMs) { Serial1.print(pollCommand); lastPoll = millis(); }
  while (Serial1.available()) {
    char c = (char)Serial1.read(); lineBuffer += c;
    if (lineBuffer.endsWith(terminator)) {
      JsonDocument doc; doc["line"] = lineBuffer; doc["seq"] = ++seqNo; doc["uptime_ms"] = millis();
      String payload; serializeJson(doc, payload); if (enabled) mqtt.publish(rawTopic.c_str(), payload.c_str()); lineBuffer = "";
    } else if (lineBuffer.length() > 256) lineBuffer = "";
  }
}
// ---- Diagnostics -----------------------------------------------------------------------------------------------
// Persistence policy: events and counters go to NVS only when something happens (reboot, Wi-Fi/MQTT outage ends) plus an
// uptime checkpoint every 10 min, so flash wear stays negligible (<1000 writes/day). Live values (RSSI, heap, uptime)
// are NOT stored on the MCU: they are published every DIAG_EVERY_MS on <prefix>/<id>/diag and kept by Django.
#define DIAG_EVERY_MS 30000
#define DIAG_RING 16
String diagTopic, bootId, lastResetKind;
uint32_t bootNo = 0, evN = 0, cBoots = 0, cPoweron = 0, cBrownout = 0, cPanic = 0, cWdt = 0, cSw = 0, cWifiDrops = 0, cMqttFails = 0;
uint32_t lastDiag = 0, lastCheckpoint = 0, wifiDownSince = 0, mqttDownSince = 0, mqttStreakFails = 0; int wifiLastReason = 0, mqttLastState = 0;
bool wifiUpOnce = false, diagDirty = true, diagFull = true, wifiDropPending = false; uint32_t wifiDropMs = 0;
void diagSaveCounters() {
  prefs.begin("diag", false); prefs.putUInt("boots", cBoots); prefs.putUInt("poweron", cPoweron); prefs.putUInt("brownout", cBrownout); prefs.putUInt("panic", cPanic);
  prefs.putUInt("wdt", cWdt); prefs.putUInt("sw", cSw); prefs.putUInt("wdrops", cWifiDrops); prefs.putUInt("mfails", cMqttFails); prefs.putUInt("evn", evN); prefs.end();
}
void diagEvent(const char *kind, const String &detail) {  // ring buffer in NVS: slot = n % DIAG_RING, value "n|bid|boot|uptime_s|kind|detail"
  evN++; String v = String(evN) + "|" + bootId + "|" + String(bootNo) + "|" + String(millis() / 1000) + "|" + kind + "|" + detail.substring(0, 80);
  prefs.begin("diag", false); prefs.putString((String("e") + String(evN % DIAG_RING)).c_str(), v); prefs.putUInt("evn", evN); prefs.end(); diagDirty = true; diagFull = true;
}
void restartFor(const char *why) {  // every intentional reboot records why, so an unexplained SW reset can be told apart
  prefs.begin("diag", false); prefs.putString("intent", why); prefs.putUInt("upck", millis() / 1000); prefs.end(); delay(100); ESP.restart();
}
void diagBoot() {
  bootId = String((uint32_t)esp_random(), HEX);
  prefs.begin("diag", false);
  bootNo = prefs.getUInt("boots", 0) + 1; cBoots = bootNo; cPoweron = prefs.getUInt("poweron", 0); cBrownout = prefs.getUInt("brownout", 0); cPanic = prefs.getUInt("panic", 0);
  cWdt = prefs.getUInt("wdt", 0); cSw = prefs.getUInt("sw", 0); cWifiDrops = prefs.getUInt("wdrops", 0); cMqttFails = prefs.getUInt("mfails", 0); evN = prefs.getUInt("evn", 0);
  String intent = prefs.getString("intent", ""); prefs.remove("intent"); uint32_t prevUp = prefs.getUInt("upck", 0); prefs.putUInt("upck", 0); prefs.end();
  esp_reset_reason_t r = esp_reset_reason(); String kind;
  switch (r) {
    case ESP_RST_POWERON: kind = "power_on"; cPoweron++; break;              // supply was lost (mains/USB unplugged) or first flash
    case ESP_RST_BROWNOUT: kind = "brownout"; cBrownout++; break;            // supply dipped below threshold
    case ESP_RST_PANIC: kind = "panic"; cPanic++; break;                     // firmware crash / exception
    case ESP_RST_INT_WDT: case ESP_RST_TASK_WDT: case ESP_RST_WDT: kind = "watchdog"; cWdt++; break;
    case ESP_RST_SW: kind = intent.length() ? intent : "sw_unknown"; cSw++; break;  // intent = which of our own paths asked for the reboot
    case ESP_RST_EXT: kind = "reset_pin"; break;                             // EN/reset button
    case ESP_RST_DEEPSLEEP: kind = "deepsleep"; break;
    default: kind = String("other_") + String((int)r);
  }
  lastResetKind = kind; diagSaveCounters();
  diagEvent("boot", kind + " prev_uptime=" + String(prevUp) + "s");
  Serial.printf("[labops] fw=%s device=%s boot#%u reset=%s prev_uptime=%us counters: poweron=%u brownout=%u panic=%u wdt=%u sw=%u\n", FW_VERSION, DEVICE_ID, (unsigned)bootNo, kind.c_str(), (unsigned)prevUp, (unsigned)cPoweron, (unsigned)cBrownout, (unsigned)cPanic, (unsigned)cWdt, (unsigned)cSw);
}
void diagWifiEvent(WiFiEvent_t e, WiFiEventInfo_t info) {  // runs in the Wi-Fi task: record only, persist from loop()
  if (e == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) { if (wifiUpOnce && !wifiDownSince) { wifiDownSince = millis(); } wifiLastReason = info.wifi_sta_disconnected.reason; }
  else if (e == ARDUINO_EVENT_WIFI_STA_GOT_IP) { if (wifiDownSince) { wifiDropMs = millis() - wifiDownSince; wifiDropPending = true; wifiDownSince = 0; } wifiUpOnce = true; }
}
void publishDiag() {
  if (!mqtt.connected()) return;
  JsonDocument d; d["up"] = millis() / 1000; d["rssi"] = WiFi.RSSI(); d["heap"] = ESP.getFreeHeap(); d["minheap"] = ESP.getMinFreeHeap(); d["boot"] = bootNo; d["reason"] = lastResetKind; d["fw"] = FW_VERSION; d["ch"] = WiFi.channel();
  JsonObject c = d["c"].to<JsonObject>(); c["boots"] = cBoots; c["poweron"] = cPoweron; c["brownout"] = cBrownout; c["panic"] = cPanic; c["wdt"] = cWdt; c["sw"] = cSw; c["wifi_drops"] = cWifiDrops; c["mqtt_fails"] = cMqttFails;
  if (diagFull) {
    JsonArray ev = d["ev"].to<JsonArray>(); prefs.begin("diag", true);
    for (int i = 0; i < DIAG_RING; i++) { String v = prefs.getString((String("e") + String(i)).c_str(), ""); if (v.length()) ev.add(v); }
    prefs.end();
  }
  String out; serializeJson(d, out); if (mqtt.publish(diagTopic.c_str(), out.c_str(), true)) { diagFull = false; diagDirty = false; }
}
void diagLoop() {  // called from loop()
  if (wifiDropPending) {
    wifiDropPending = false; cWifiDrops++; diagSaveCounters();
    diagEvent("wifi_drop", "reason=" + String(wifiLastReason) + " down=" + String(wifiDropMs / 1000.0f, 1) + "s");
  }
  if (millis() - lastCheckpoint > 600000UL) { lastCheckpoint = millis(); prefs.begin("diag", false); prefs.putUInt("upck", millis() / 1000); prefs.end(); }
  if (millis() - lastDiag > DIAG_EVERY_MS || (diagFull && mqtt.connected() && millis() - lastDiag > 2000)) { lastDiag = millis(); publishDiag(); }
}
void abandonTrial() {
  prefs.begin("wifi", false); prefs.remove("nssid"); prefs.remove("npass"); prefs.remove("nuser"); prefs.remove("nident"); prefs.end(); restartFor("wifi_trial_revert");
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
  else if (action == "ota") {
    JsonDocument o; if (deserializeJson(o, arg)) { ack(id, false, "bad ota arg"); return; }
    if (otaPending) { ack(id, false, "ota already running"); return; }
    otaImage = o["image"] | 0; otaSize = o["size"] | 0; otaVersion = String((const char *)(o["version"] | "")); otaSha = String((const char *)(o["sha256"] | ""));
    if (!otaImage || !otaSize || otaSha.length() != 64) { ack(id, false, "ota arg needs image, size, sha256"); return; }
    otaCmdId = id; otaPending = true;  // signed arg => size + sha256 are authenticated; image bytes are verified against them
  }
  else if (action == "set_wifi") {
    JsonDocument w; String ns, np;
    if (deserializeJson(w, arg) || !(ns = String((const char *)(w["ssid"] | ""))).length() || ns.length() > 32) { ack(id, false, "arg must be {\"ssid\":..,\"pass\":..[,\"eap_user\":..,\"eap_identity\":..]}"); return; }
    np = String((const char *)(w["pass"] | ""));
    prefs.begin("wifi", false); prefs.putString("nssid", ns); prefs.putString("npass", np);
    prefs.putString("nuser", String((const char *)(w["eap_user"] | ""))); prefs.putString("nident", String((const char *)(w["eap_identity"] | ""))); prefs.end();  // eap_user => WPA2-Enterprise, "pass" is then the EAP password
    ack(id, true, "stored pending creds, rebooting; reverts in " + String(TRIAL_MS / 1000) + "s if MQTT does not connect"); mqtt.loop(); delay(200); restartFor("set_wifi");
  }
  else if (action == "reset_config") { clearConfig(); ack(id, true, "device config cleared, rebooting (retained config will be re-applied)"); mqtt.loop(); delay(200); restartFor("reset_config"); }
  else if (action == "factory_reset") {
    clearConfig(); prefs.begin("wifi", false); prefs.clear(); prefs.end();
    ack(id, true, "factory reset: config + stored Wi-Fi cleared, rebooting on secrets.h Wi-Fi"); mqtt.loop(); delay(200); restartFor("factory_reset");
  }
  else if (action == "diag") { diagFull = true; publishDiag(); ack(id, true, "diagnostics published"); }
  else if (action == "reboot") { ack(id, true, "rebooting"); mqtt.loop(); delay(200); restartFor("cmd_reboot"); }
  else ack(id, false, "unknown action");
}
#ifdef USB_MSC_UPLOAD
#include "usb_drop.h"  // needs mqtt, hmacHex, fileBase, ackXfer defined above
#endif
// Pulls the signed image 4 KB at a time over MQTT (stop-and-wait: request index n, wait for ota/<image>/<n>), streams it to the
// inactive OTA partition while hashing, and only switches boot partition if the SHA-256 matches the signed command.
void otaRun() {
  otaPending = false; uint32_t t0 = millis(), chunks = (otaSize + 4095) / 4096; mbedtls_md_context_t ctx; mbedtls_md_init(&ctx);
  auto fail = [&](const String &why) { Update.abort(); mbedtls_md_free(&ctx); ack(otaCmdId, false, why); };
  if (!Update.begin(otaSize)) { ack(otaCmdId, false, String("no room / bad size: ") + Update.errorString()); mbedtls_md_free(&ctx); return; }
  mbedtls_md_setup(&ctx, mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), 0); mbedtls_md_starts(&ctx);
  for (uint32_t i = 0; i < chunks; i++) {
    bool got = false;
    for (int attempt = 0; attempt < 4 && !got; attempt++) {
      otaLen = -1; JsonDocument r; r["image"] = otaImage; r["index"] = i; String req; serializeJson(r, req); mqtt.publish(otaReqTopic.c_str(), req.c_str());
      for (uint32_t t = millis(); millis() - t < 4000 && !got;) { mqtt.loop(); pumpSerial(); if (otaLen >= 0 && otaIdx == i) got = true; else delay(2); }
    }
    if (!got) return fail("ota timeout at chunk " + String(i));
    uint32_t want = min((uint32_t)4096, otaSize - i * 4096);
    if ((uint32_t)otaLen != want) return fail("ota chunk " + String(i) + " has wrong length");
    mbedtls_md_update(&ctx, otaBuf, want);
    if (Update.write(otaBuf, want) != want) return fail(String("flash write failed: ") + Update.errorString());
  }
  uint8_t h[32]; mbedtls_md_finish(&ctx, h); mbedtls_md_free(&ctx); String hex; char b[3]; for (int i = 0; i < 32; i++) { snprintf(b, 3, "%02x", h[i]); hex += b; }
  if (hex != otaSha) { Update.abort(); ack(otaCmdId, false, "sha256 mismatch, image discarded"); return; }
  if (!Update.end(true)) { ack(otaCmdId, false, String("finalize failed: ") + Update.errorString()); return; }
  float secs = (millis() - t0) / 1000.0f; ack(otaCmdId, true, "flashed v" + otaVersion + " in " + String(secs, 1) + "s (" + String(otaSize / 1024.0f / secs, 1) + " KB/s), rebooting"); mqtt.loop(); delay(300); restartFor("ota");
}
void onMessage(char *topic, byte *payload, unsigned int length) {
  String t(topic);
  if (t.startsWith(otaBase)) { otaIdx = t.substring(t.lastIndexOf('/') + 1).toInt(); otaLen = length <= sizeof(otaBuf) ? (int)length : -2; if (otaLen > 0) memcpy(otaBuf, payload, length); return; }
  if (t == fileAckTopic) { JsonDocument a; if (!deserializeJson(a, payload, length)) { ackState = (a["ok"] | false) ? 1 : 2; ackXfer = a["xfer"] | 0; } return; }
  if (t == cmdTopic) { onCommand(payload, length); return; } if (t != configTopic) return;
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
  if (wifiUser.length()) { const char *eapId = wifiIdent.length() ? wifiIdent.c_str() : wifiUser.c_str(); EAP_SET(eapId, wifiUser.c_str(), wifiPass.c_str()); WiFi.begin(wifiSsid.c_str()); }
  else WiFi.begin(wifiSsid.c_str(), wifiPass.c_str());
}
void connectWifi() {
  if (WiFi.status() == WL_CONNECTED) return; Serial.printf("[labops] wifi: joining '%s'%s\n", wifiSsid.c_str(), wifiUser.length() ? " (WPA2-Enterprise)" : ""); beginWifi();
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    if (wifiTrial && millis() - bootMs > TRIAL_MS) abandonTrial();
  }
}
bool mqttUpOnce = false;
void connectMqtt() {
  if (!mqttDownSince) { mqttDownSince = millis(); mqttStreakFails = 0; }
  while (!mqtt.connected()) {
    String will = "{\"state\":\"offline\",\"config_version\":" + String(configVersion) + ",\"fw\":\"" FW_VERSION "\"}";
    if (mqtt.connect(DEVICE_ID, statusTopic.c_str(), 1, true, will.c_str())) {
      mqtt.subscribe(configTopic.c_str()); mqtt.subscribe(cmdTopic.c_str(), 1); mqtt.subscribe(fileAckTopic.c_str()); mqtt.subscribe((otaBase + "#").c_str());
      if (wifiTrial) commitWifi();
      publishStatus("online");
      if (mqttUpOnce && (mqttStreakFails || millis() - mqttDownSince > 5000)) {  // a real outage, not the first connect after boot
        cMqttFails += mqttStreakFails; diagSaveCounters();
        diagEvent(mqttStreakFails ? "mqtt_fail" : "mqtt_drop", "fails=" + String(mqttStreakFails) + " last_state=" + String(mqttLastState) + " down=" + String((millis() - mqttDownSince) / 1000.0f, 1) + "s");
      }
      mqttUpOnce = true; mqttDownSince = 0; diagFull = true;
    } else {
      mqttLastState = mqtt.state(); mqttStreakFails++;  // PubSubClient state: -2 unreachable, -4 timeout, 5 not authorized...
      if (wifiTrial && millis() - bootMs > TRIAL_MS) abandonTrial();
      delay(2000);
    }
  }
}
void setup() {
  Serial.begin(115200); rawTopic = String(PREFIX) + "/" DEVICE_ID "/raw"; configTopic = String(PREFIX) + "/" DEVICE_ID "/config"; statusTopic = String(PREFIX) + "/" DEVICE_ID "/status"; cmdTopic = String(PREFIX) + "/" DEVICE_ID "/cmd"; ackTopic = String(PREFIX) + "/" DEVICE_ID "/ack"; fileBase = String(PREFIX) + "/" DEVICE_ID "/file"; fileAckTopic = String(PREFIX) + "/" DEVICE_ID "/file_ack"; otaReqTopic = String(PREFIX) + "/" DEVICE_ID "/ota_req"; otaBase = String(PREFIX) + "/" DEVICE_ID "/ota/"; diagTopic = String(PREFIX) + "/" DEVICE_ID "/diag";
  prefs.begin("scale", true); lastCmdId = prefs.getUInt("cmdid", 0); prefs.end();
  bootMs = millis(); diagBoot(); WiFi.onEvent(diagWifiEvent); loadWifi();
  loadConfig();
#ifdef USB_MSC_UPLOAD
  usbDropBegin();
#endif
  beginWifi(); mqtt.setServer(MQTT_HOST, MQTT_PORT); mqtt.setCallback(onMessage); mqtt.setBufferSize(4096 + 160);  // 4 KB file chunks + topic/header
}
void loop() {
  if (WiFi.status() != WL_CONNECTED) connectWifi();
  if (!mqtt.connected()) connectMqtt();
  mqtt.loop();
  diagLoop();
  if (otaPending) otaRun();
#ifdef USB_MSC_UPLOAD
  usbDropPoll();
#endif
  pumpSerial();
}
