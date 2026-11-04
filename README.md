# Lab-ops device integration

An open source integration layer for balances in a pharma QC lab. ESP32-S3 firmware forwards raw RS232 lines; Django loads the device's versioned parser schema from PostgreSQL, parses and stores traceable readings, and publishes retained configuration for the device. It is the device-integration layer referenced by [lab-ops-dashboard](https://github.com/tautvydas-jocius/lab-ops-dashboard), where MES test protocols use balances.

```text
Balance RS232 -> ESP32-S3 -> MQTT -> Django listener -> parser schema from DB -> Postgres -> JSON API -> MES
                                      ^
                          Django admin -> retained MQTT config -> ESP32-S3
```

## System diagrams

- [Device integrations](docs/device-integrations.html) — data and configuration paths across the balance, firmware, MQTT, Django, PostgreSQL, and MES API. Editable source: [architecture JSON](docs/device-integrations.architecture.json).
- [Where readings stall](docs/device-stall-points.html) — current failure and exception paths in the gateway. This is a code-path map; the system does not currently record stall duration or frequency. Editable source: [lifecycle JSON](docs/device-stall-points.lifecycle.json).

## MQTT topics

Default prefix: `lab/devices` (override with `MQTT_PREFIX`).

| Topic | Direction | Retained | Payload |
|---|---|---:|---|
| `{prefix}/{device_id}/raw` | device → server | no | `{"line":"…","seq":1,"uptime_ms":1234}` |
| `{prefix}/{device_id}/config` | server → device | yes | Serial, polling, enabled, and `config_version` settings |
| `{prefix}/{device_id}/status` | device → server | yes | `{"state":"online","config_version":1,"fw":"1.0.0"}`; offline is the LWT |

## Parser schemas and traceability

Every device points to a `ParserSchema` row identified by `(name, version)`. Readings keep a foreign key to the exact schema used when they were parsed. Once readings reference a schema, changing its pattern or stability rules raises “create a new version”; create a new schema row and assign devices to it instead. Balance-specific parsing stays server-side, so adding a model requires a parser schema and no firmware change.

## Quickstart

```sh
docker compose up -d --build
# Optional simulated balances
docker compose --profile sim up -d simulator
# Create a Django admin user
docker compose exec web python manage.py createsuperuser
curl http://127.0.0.1:8011/api/devices/
curl 'http://127.0.0.1:8011/api/devices/bal-01/readings/?limit=10'
```

Admin is at `http://127.0.0.1:8011/admin/`. The API exposes `GET /api/devices/`, `GET /api/devices/{device_id}/` (including current config), and `GET /api/devices/{device_id}/readings/?limit=50&stable=1`. The MES can pull weights there. Web and MQTT host ports bind to loopback only; PostgreSQL is not published.

Settings come from environment variables: `DATABASE_URL` or `POSTGRES_*`, `MQTT_HOST`, `MQTT_PORT`, `MQTT_PREFIX`, `DEBUG`, `SECRET_KEY`, and `ALLOWED_HOSTS`.

## Remote actions (admin -> MCU)

Actions: `ping`, `poll_now`, `send_serial` (arg = text), `reboot`, `set_wifi` (arg = `{"ssid":"..","pass":".."}`), `reset_config`, `factory_reset`. Trigger them from Django admin (Devices list -> action menu, or Device commands -> Add) or the token-protected API:

```sh
export CONTROL_API_TOKEN=$(openssl rand -hex 24)   # set before docker compose up; API is disabled (403) when unset
curl -X POST -H "Authorization: Bearer $CONTROL_API_TOKEN" -d '{"action":"ping"}' http://127.0.0.1:8011/api/devices/bal-01/actions/
curl -H "Authorization: Bearer $CONTROL_API_TOKEN" http://127.0.0.1:8011/api/devices/bal-01/actions/   # history + acks
```

Server publishes `{prefix}/{id}/cmd` `{"id","action","arg","sig"}`; the MCU answers on `{prefix}/{id}/ack` `{"id","ok","result"}`. `sig` is HMAC-SHA256 over `id|action|arg` keyed with the per-device command token (Device admin page, read-only); the token never travels over MQTT. Put it in `secrets.h` as `COMMAND_TOKEN`. The firmware drops bad signatures silently and rejects non-increasing ids (replay protection, persisted in NVS). Not encrypted: use MQTT TLS and broker ACLs outside a trusted LAN.

### Wi-Fi credentials and reset (NVS)

The MCU keeps Wi-Fi in flash (NVS namespace `wifi`); `secrets.h` is only the fallback. `set_wifi` stores the new credentials as *pending*, reboots, and trials them: they become active only after MQTT connects, otherwise the MCU drops them after 45 s and reboots on the old ones, so a typo cannot strand a remote device. The password is sent signed but unencrypted over MQTT (use TLS) and is never saved in Device commands history, only the SSID. `reset_config` wipes the stored serial/poll config (the retained server config is re-applied on reconnect); `factory_reset` also wipes stored Wi-Fi, returning to `secrets.h`. Both keep the replay counter.

### WPA2-Enterprise (EAP) Wi-Fi

`set_wifi` also takes `eap_user` (and optional `eap_identity`): `{"ssid":"corp","pass":"<EAP password>","eap_user":"user@corp","eap_identity":"anon@corp"}` joins PEAP/MSCHAPv2 networks, with the same pending-then-commit rollback as above. No server certificate is validated (certificate-pinned EAP-TLS is not implemented). History records only `ssid=.. eap=<user>`.

### USB drop drive (CSV / PDF / TXT straight to the server)

With `USB_MSC_UPLOAD` defined, the ESP32-S3's native USB port (GPIO19/20) shows up as a 4 MB flash drive named `LABOPS DROP` (RAM-backed FAT12 in PSRAM; 256 KB without PSRAM; empty after every reboot). Copy a `.csv`, `.pdf` or `.txt` onto it; about 2 s after the host stops writing, the MCU reads the file and sends it over MQTT. Unchanged files are not re-sent; edited ones are; a missing ack triggers a retry.

Files are opaque bytes end to end: never decoded as text and never passed to a parser schema (a CSV may be UTF-16, a PDF is binary). The MCU publishes `{prefix}/{id}/file/{xfer}/meta` (JSON: name, size, chunks, sha256, HMAC `sig` keyed with the device command token) and then raw 4 KB binary chunks on `{prefix}/{id}/file/{xfer}/{seq}`. The `mqtt_listener` watcher reassembles (chunks may arrive in any order; incomplete transfers expire after 60 s), checks signature, size and SHA-256, validates the type (`.csv`/`.txt`/`.pdf`; PDFs must start `%PDF`; 8 MB cap), stores it in the `media-data` volume and answers on `{prefix}/{id}/file_ack`. Browse in admin (Uploaded files) or via the API with `CONTROL_API_TOKEN`: `GET /api/devices/{id}/files/list/`, `GET /api/files/{pk}/`. Needs `ARDUINO_USB_MODE=0` (already in `platformio.ini`); pick `board_build.arduino.memory_type` to match your PSRAM. Broker note: keep `message_size_limit` at the Mosquitto default (unlimited) or at least 5 KB.

## Firmware

Install PlatformIO, copy `firmware/include/secrets.example.h` to `firmware/include/secrets.h`, set WiFi/MQTT values and `DEVICE_ID`, then build and flash from `firmware/`:

```sh
pio run
pio run --target upload
```

UART1 uses RX GPIO18 and TX GPIO17. Firmware stores the last config in NVS and uses it before MQTT returns. It forwards lines without parsing.

## License

GPL-3.0. See [LICENSE](LICENSE).
