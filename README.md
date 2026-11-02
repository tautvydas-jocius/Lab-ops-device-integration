# Lab-ops device integration

An open source integration layer for balances in a pharma QC lab. ESP32-S3 firmware forwards raw RS232 lines; Django loads the device's versioned parser schema from PostgreSQL, parses and stores traceable readings, and publishes retained configuration for the device. It is the device-integration layer referenced by [lab-ops-dashboard](https://github.com/tautvydas-jocius/lab-ops-dashboard), where MES test protocols use balances.

```text
Balance RS232 -> ESP32-S3 -> MQTT -> Django listener -> parser schema from DB -> Postgres -> JSON API -> MES
                                      ^
                          Django admin -> retained MQTT config -> ESP32-S3
```

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

## Firmware

Install PlatformIO, copy `firmware/include/secrets.example.h` to `firmware/include/secrets.h`, set WiFi/MQTT values and `DEVICE_ID`, then build and flash from `firmware/`:

```sh
pio run
pio run --target upload
```

UART1 uses RX GPIO18 and TX GPIO17. Firmware stores the last config in NVS and uses it before MQTT returns. It forwards lines without parsing.

## License

GPL-3.0. See [LICENSE](LICENSE).
