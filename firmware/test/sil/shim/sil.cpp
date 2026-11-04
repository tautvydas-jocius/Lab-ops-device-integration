// Hardware model behind the shim headers, plus a control FIFO ($SIL_STATE/ctl) the test driver uses to play the outside world:
//   uart line <text> | uart rate <lines/s>      the balance
//   wifi down <secs> | wifi rssi <dBm>          the radio
//   mqtt block <secs>                           broker unreachable while Wi-Fi stays up
//   fault brownout|panic|watchdog|power         what the next esp_reset_reason() reports, then restart
//   usb put <file> <name> | usb del <name>      the PC, through mtools (an independent FAT implementation) on the image read over MSC
//   stats                                       write $SIL_STATE/stats.txt
#include <Arduino.h>
#include <WiFi.h>
#include <Preferences.h>
#include <Update.h>
#include <USB.h>
#include <USBMSC.h>
#include <esp_eap_client.h>
#include <arpa/inet.h>
#include <fcntl.h>
#include <netdb.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <netinet/tcp.h>
#include <unistd.h>
#include <atomic>
#include <chrono>
#include <cstdarg>
#include <deque>
#include <fstream>
#include <mutex>
#include <random>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

static char **g_argv;
static std::string STATE() { const char *s = getenv("SIL_STATE"); return s ? s : "/state"; }
static std::string P(const std::string &f) { return STATE() + "/" + f; }
static const auto T0 = std::chrono::steady_clock::now();
unsigned long millis() { return std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - T0).count(); }
void delay(unsigned long ms) { usleep(ms * 1000); }
size_t sil_strlcpy(char *d, const char *s, size_t n) { size_t l = strlen(s); if (n) { size_t c = l < n - 1 ? l : n - 1; memcpy(d, s, c); d[c] = 0; } return l; }
uint32_t esp_random() { static std::random_device r; return r(); }
bool psramFound() { const char *p = getenv("SIL_PSRAM"); return p && p[0] == '1'; }
void *ps_malloc(size_t n) { return malloc(n); }
static std::string slurp(const std::string &p) { std::ifstream f(p, std::ios::binary); std::stringstream b; b << f.rdbuf(); return b.str(); }
static void spit(const std::string &p, const std::string &v) { std::ofstream f(p, std::ios::binary); f << v; }

// ---- UART ----------------------------------------------------------------------------------------------------------
static std::mutex uartM; static std::deque<char> rxq; static size_t rxCap = 256;  // Arduino-ESP32 default until setRxBufferSize()
static std::atomic<uint64_t> uartLinesFed{0}, uartLinesDamaged{0}; static std::atomic<int> uartRate{0};
static void uartFeed(const std::string &line) {  // a real FIFO drops whatever does not fit
  std::lock_guard<std::mutex> g(uartM); uartLinesFed++;
  if (rxq.size() + line.size() > rxCap) { uartLinesDamaged++; return; }
  rxq.insert(rxq.end(), line.begin(), line.end());
}
HardwareSerial Serial(0), Serial1(1);
void HardwareSerial::begin(unsigned long baud, uint32_t cfg, int rx, int tx) { if (num) ::printf("[sil] Serial1.begin baud=%lu cfg=%u rx=%d tx=%d rxbuf=%zu\n", baud, cfg, rx, tx, rxCap); }
void HardwareSerial::end() { if (num) { std::lock_guard<std::mutex> g(uartM); rxq.clear(); } }
size_t HardwareSerial::setRxBufferSize(size_t n) { if (num) rxCap = n; return n; }
int HardwareSerial::available() { if (!num) return 0; std::lock_guard<std::mutex> g(uartM); return rxq.size(); }
int HardwareSerial::read() { std::lock_guard<std::mutex> g(uartM); if (rxq.empty()) return -1; char c = rxq.front(); rxq.pop_front(); return (uint8_t)c; }
size_t HardwareSerial::print(const String &x) {
  if (!num) { fputs(x.c_str(), stdout); return x.length(); }
  std::ofstream(P("uart_tx.log"), std::ios::app) << x.c_str() << "\n";  // what the balance received
  uartFeed("S S      " + std::to_string(12.3456 + (esp_random() % 10) / 1000.0).substr(0, 7) + " g\r\n");  // balance answers a poll
  return x.length();
}
int HardwareSerial::printf(const char *fmt, ...) { va_list a; va_start(a, fmt); int n = vprintf(fmt, a); va_end(a); fflush(stdout); return n; }

// ---- reset + OTA slot ----------------------------------------------------------------------------------------------
EspClass ESP;
esp_reset_reason_t esp_reset_reason() {
  std::string r = slurp(P("next_reset")); unlink(P("next_reset").c_str());
  if (r == "sw") return ESP_RST_SW; if (r == "brownout") return ESP_RST_BROWNOUT; if (r == "panic") return ESP_RST_PANIC;
  if (r == "watchdog") return ESP_RST_TASK_WDT; return ESP_RST_POWERON;
}
static void reexec() {
  fflush(stdout);
  std::string boot = P("ota_boot.bin");
  if (access(boot.c_str(), X_OK) == 0) { char *a[] = {(char *)boot.c_str(), nullptr}; execv(boot.c_str(), a); }  // the flashed image really runs
  execv(g_argv[0], g_argv); _exit(9);
}
void EspClass::restart() { spit(P("next_reset"), "sw"); ::printf("[sil] ESP.restart()\n"); reexec(); _exit(9); }
uint32_t EspClass::getFreeHeap() { return 210000; }
uint32_t EspClass::getMinFreeHeap() { return 180000; }
UpdateClass Update; static FILE *otaF; static size_t otaWant, otaGot; static const char *otaErr = "no error";
bool UpdateClass::begin(size_t n) { if (n > 3342336) { otaErr = "image larger than OTA slot"; return false; } otaF = fopen(P("ota_slot.bin").c_str(), "wb"); otaWant = n; otaGot = 0; return otaF; }
size_t UpdateClass::write(uint8_t *b, size_t n) { if (!otaF) return 0; otaGot += n; return fwrite(b, 1, n, otaF); }
bool UpdateClass::end(bool) { if (!otaF) return false; fclose(otaF); otaF = nullptr; if (otaGot != otaWant) { otaErr = "short image"; return false; } chmod(P("ota_slot.bin").c_str(), 0755); rename(P("ota_slot.bin").c_str(), P("ota_boot.bin").c_str()); return true; }
void UpdateClass::abort() { if (otaF) { fclose(otaF); otaF = nullptr; } unlink(P("ota_slot.bin").c_str()); ::printf("[sil] Update.abort()\n"); }
const char *UpdateClass::errorString() { return otaErr; }

// ---- Preferences: one file per namespace, ESP32 rules enforced ------------------------------------------------------
static void nvsViolation(const char *what, const char *k) { ::printf("[sil] NVS VIOLATION: %s (%s)\n", what, k); std::ofstream(P("violations.log"), std::ios::app) << what << " " << k << "\n"; }
static std::string hexEnc(const std::string &s) { std::string o; char b[3]; for (unsigned char c : s) { snprintf(b, 3, "%02x", c); o += b; } return o; }
static std::string hexDec(const std::string &s) { std::string o; for (size_t i = 0; i + 1 < s.size(); i += 2) o += (char)strtol(s.substr(i, 2).c_str(), nullptr, 16); return o; }
bool Preferences::begin(const char *ns, bool ro) {
  if (started) { nvsViolation("begin() while already open", ns); return false; }
  std::string path = P("nvs_" + std::string(ns));
  if (ro && access(path.c_str(), R_OK) != 0) { m.clear(); return false; }  // nvs_open(READONLY) on a missing namespace fails on the chip too
  m.clear(); std::ifstream f(path); std::string k, v; while (f >> k >> v) m[hexDec(k)] = hexDec(v == "-" ? "" : v);
  started = true; ro_ = ro; ns_ = ns; if (!ro) flush(); return true;
}
void Preferences::end() { started = false; }
void Preferences::flush() { std::ofstream f(P("nvs_" + ns_)); for (auto &e : m) f << hexEnc(e.first) << " " << (e.second.empty() ? "-" : hexEnc(e.second)) << "\n"; }
bool Preferences::get(const char *k, std::string &out) { if (!started) return false; if (strlen(k) > 15) nvsViolation("key longer than 15 chars", k); auto i = m.find(k); if (i == m.end()) return false; out = i->second; return true; }
size_t Preferences::put(const char *k, const std::string &v) { if (!started || ro_) { nvsViolation("put on closed/read-only namespace", k); return 0; } if (strlen(k) > 15) { nvsViolation("key longer than 15 chars", k); return 0; } m[k] = v; flush(); return v.size() + 1; }
bool Preferences::clear() { if (!started || ro_) return false; m.clear(); flush(); return true; }
bool Preferences::remove(const char *k) { if (!started || ro_) return false; m.erase(k); flush(); return true; }
uint32_t Preferences::getUInt(const char *k, uint32_t d) { std::string v; return get(k, v) ? strtoul(v.c_str(), nullptr, 10) : d; }
size_t Preferences::putUInt(const char *k, uint32_t v) { return put(k, std::to_string(v)); }
uint8_t Preferences::getUChar(const char *k, uint8_t d) { return getUInt(k, d); }
size_t Preferences::putUChar(const char *k, uint8_t v) { return putUInt(k, v); }
int8_t Preferences::getChar(const char *k, int8_t d) { return getUInt(k, d); }
size_t Preferences::putChar(const char *k, int8_t v) { return putUInt(k, v); }
bool Preferences::getBool(const char *k, bool d) { return getUInt(k, d); }
size_t Preferences::putBool(const char *k, bool v) { return putUInt(k, v); }
String Preferences::getString(const char *k, const String &d) { std::string v; return get(k, v) ? String(v) : d; }
size_t Preferences::putString(const char *k, const String &v) { return put(k, v.s); }

// ---- Wi-Fi model ---------------------------------------------------------------------------------------------------
WiFiClass WiFi; static WiFiEventSysCb wifiCb; static std::atomic<bool> wifiUp{false}; static std::atomic<int> wifiRssi{-58};
static std::atomic<unsigned long> wifiDownUntil{0}, mqttBlockUntil{0}; static std::string eapUser, eapPass, eapId, wantSsid, wantPass; static bool eapOn = false;
static std::mutex sockM; static std::vector<int> socks;
esp_err_t esp_eap_client_set_identity(const unsigned char *v, int n) { eapId.assign((const char *)v, n); return 0; }
esp_err_t esp_eap_client_set_username(const unsigned char *v, int n) { eapUser.assign((const char *)v, n); return 0; }
esp_err_t esp_eap_client_set_password(const unsigned char *v, int n) { eapPass.assign((const char *)v, n); return 0; }
esp_err_t esp_wifi_sta_enterprise_enable() { eapOn = true; return 0; }
static void fire(WiFiEvent_t e, uint8_t reason) { if (wifiCb) { WiFiEventInfo_t i{}; i.wifi_sta_disconnected.reason = reason; wifiCb(e, i); } }
static bool credsOk() {  // SIL_WIFI: "ssid:pass" or "ssid:eapuser:eappass", comma separated
  std::stringstream all(getenv("SIL_WIFI") ? getenv("SIL_WIFI") : "lab:labpass"); std::string net;
  while (std::getline(all, net, ',')) {
    if (eapOn) { if (net == wantSsid + ":" + eapUser + ":" + eapPass) return true; }
    else if (net == wantSsid + ":" + wantPass) return true;
  }
  return false;
}
void WiFiClass::begin(const char *ssid, const char *pass) {
  wantSsid = ssid; wantPass = pass ? pass : "";
  ::printf("[sil] WiFi.begin ssid=%s mode=%s\n", ssid, eapOn ? ("EAP user=" + eapUser + " identity=" + eapId).c_str() : "PSK"); fflush(stdout);
}
int WiFiClass::status() {
  bool want = credsOk() && millis() >= wifiDownUntil.load();
  if (want && !wifiUp) { wifiUp = true; ::printf("[sil] wifi up ssid=%s\n", wantSsid.c_str()); fire(ARDUINO_EVENT_WIFI_STA_GOT_IP, 0); }
  return wifiUp ? WL_CONNECTED : WL_DISCONNECTED;
}
int WiFiClass::RSSI() { return wifiRssi; }
void WiFiClass::onEvent(WiFiEventSysCb cb) { wifiCb = cb; }
static void dropSockets() { std::lock_guard<std::mutex> g(sockM); for (int s : socks) shutdown(s, SHUT_RDWR); }

// ---- TCP (real sockets to the real broker) ---------------------------------------------------------------------------
int WiFiClient::connect(IPAddress ip, uint16_t port) { char h[32]; snprintf(h, sizeof h, "%d.%d.%d.%d", ip[0], ip[1], ip[2], ip[3]); return connect(h, port); }
int WiFiClient::connect(const char *host, uint16_t port) {
  stop(); if (!wifiUp || millis() < mqttBlockUntil.load()) return 0;
  addrinfo hints{}, *r = nullptr; hints.ai_socktype = SOCK_STREAM; hints.ai_family = AF_INET;
  if (getaddrinfo(host, std::to_string(port).c_str(), &hints, &r) || !r) return 0;
  int s = socket(r->ai_family, r->ai_socktype | SOCK_CLOEXEC, 0);
  int rc = ::connect(s, r->ai_addr, r->ai_addrlen); freeaddrinfo(r);
  if (rc) { close(s); return 0; }
  int one = 1; setsockopt(s, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
  fd = s; { std::lock_guard<std::mutex> g(sockM); socks.push_back(s); } return 1;
}
size_t WiFiClient::write(const uint8_t *b, size_t n) { if (fd < 0) return 0; size_t d = 0; while (d < n) { ssize_t k = send(fd, b + d, n - d, MSG_NOSIGNAL); if (k <= 0) return d; d += k; } return d; }
int WiFiClient::available() { if (fd < 0) return 0; int n = 0; ioctl(fd, FIONREAD, &n); return n; }
int WiFiClient::read() { uint8_t c; return read(&c, 1) == 1 ? c : -1; }
int WiFiClient::read(uint8_t *b, size_t n) { if (fd < 0) return -1; return recv(fd, b, n, MSG_DONTWAIT); }
int WiFiClient::peek() { uint8_t c; return fd >= 0 && recv(fd, &c, 1, MSG_PEEK | MSG_DONTWAIT) == 1 ? c : -1; }
void WiFiClient::stop() { if (fd >= 0) { { std::lock_guard<std::mutex> g(sockM); socks.erase(std::remove(socks.begin(), socks.end(), fd), socks.end()); } close(fd); fd = -1; } }
uint8_t WiFiClient::connected() { if (fd < 0) return 0; uint8_t c; ssize_t k = recv(fd, &c, 1, MSG_PEEK | MSG_DONTWAIT); if (k == 0) return 0; if (k < 0 && errno != EAGAIN && errno != EWOULDBLOCK) return 0; return 1; }

// ---- USB mass storage: the "PC" is mtools working on the sectors read over the MSC callbacks ---------------------------
USBClass USB; static USBMSC *theMsc;
bool USBMSC::begin(uint32_t s, uint16_t z) { sectors = s; size = z; theMsc = this; ::printf("[sil] USB MSC drive %u KB\n", s * z / 1024); return true; }
static std::string usbRead() { std::string img(theMsc->sectors * theMsc->size, 0); for (uint32_t l = 0; l < theMsc->sectors; l++) theMsc->rd(l, 0, &img[l * theMsc->size], theMsc->size); return img; }
static void usbWriteBack(const std::string &oldI, const std::string &newI) {  // only changed sectors, like a host would
  int changed = 0; std::string sec;
  for (uint32_t l = 0; l < theMsc->sectors; l++) {
    if (memcmp(&oldI[l * theMsc->size], &newI[l * theMsc->size], theMsc->size)) { sec = newI.substr(l * theMsc->size, theMsc->size); theMsc->wr(l, 0, (uint8_t *)&sec[0], theMsc->size); changed++; }
  }
  ::printf("[sil] usb: host wrote %d sectors\n", changed);
}
static void usbHost(const std::string &op, const std::string &a, const std::string &b) {
  std::string img = P("usb.img"), before = usbRead(); spit(img, before);
  int fsck = system(("fsck.vfat -n '" + img + "' > " + P("fsck.txt") + " 2>&1").c_str());
  std::string cmd = op == "put" ? "mcopy -o -i '" + img + "' '" + a + "' '::" + b + "'" : "mdel -i '" + img + "' '::" + a + "'";
  int rc = system((cmd + " > " + P("mtools.txt") + " 2>&1").c_str());
  ::printf("[sil] usb %s %s: fsck_before=%d mtools_rc=%d\n", op.c_str(), a.c_str(), WEXITSTATUS(fsck), WEXITSTATUS(rc)); usbWriteBack(before, slurp(img));
  spit(P("usb_last.txt"), std::to_string(WEXITSTATUS(fsck)) + " " + std::to_string(WEXITSTATUS(rc)) + "\n");
}

// ---- control ---------------------------------------------------------------------------------------------------------
static void control() {
  mkfifo(P("ctl").c_str(), 0666);
  for (;;) {
    std::ifstream f(P("ctl")); std::string line;
    while (std::getline(f, line)) {
      std::stringstream ss(line); std::string w, a, b; ss >> w >> a; std::getline(ss, b); if (!b.empty() && b[0] == ' ') b.erase(0, 1);
      if (w == "uart" && a == "line") uartFeed(b + "\r\n");
      else if (w == "uart" && a == "rate") uartRate = atoi(b.c_str());
      else if (w == "wifi" && a == "down") { wifiDownUntil = millis() + atoi(b.c_str()) * 1000; wifiUp = false; dropSockets(); ::printf("[sil] wifi down %ss\n", b.c_str()); fire(ARDUINO_EVENT_WIFI_STA_DISCONNECTED, 200); }
      else if (w == "wifi" && a == "rssi") wifiRssi = atoi(b.c_str());
      else if (w == "mqtt" && a == "block") { mqttBlockUntil = millis() + atoi(b.c_str()) * 1000; dropSockets(); ::printf("[sil] mqtt blocked %ss\n", b.c_str()); }
      else if (w == "fault") { spit(P("next_reset"), a); ::printf("[sil] FAULT %s\n", a.c_str()); reexec(); }
      else if (w == "usb" && theMsc) { std::stringstream rest(b); std::string x, y; rest >> x; std::getline(rest, y); if (!y.empty() && y[0] == ' ') y.erase(0, 1); usbHost(a, x, y); }
      else if (w == "stats") spit(P("stats.txt"), "fed=" + std::to_string(uartLinesFed.load()) + " damaged=" + std::to_string(uartLinesDamaged.load()) + "\n");
      fflush(stdout);
    }
  }
}
static void balance() { uint64_t i = 0; for (;;) { int r = uartRate; if (r <= 0) { usleep(50000); continue; } usleep(1000000 / r); i++; char l[48]; snprintf(l, sizeof l, "S S      %.4f g\r\n", 12.3 + (i % 100) / 10000.0); uartFeed(l); } }

int main(int, char **argv) {
  g_argv = argv; setvbuf(stdout, nullptr, _IOLBF, 0);
  // Keep the path of the first image so a later restart without OTA still has something to run
  std::thread(control).detach(); std::thread(balance).detach();
  setup();
  for (;;) { loop(); usleep(100); }
}
