#!/usr/bin/env bash
# Boots the real firmware.bin in Espressif's QEMU (esp32s3 machine) for ~25 s and saves the serial log.
# Boot smoke test only: QEMU does not emulate the S3 Wi-Fi, so MQTT/Django are not reachable from here.
# RESULT SO FAR: the ROM and bootloader run and the app reaches IDF startup, then asserts in esp_system init_flash
# (startup_funcs.c:118) before setup() is ever called, with PSRAM or DIO flash variants alike: Arduino's prebuilt
# libs do not match QEMU's flash model. Needs a custom sdkconfig (ESP-IDF build) to get further. Not a firmware bug.
# Usage: firmware/tools/qemu_smoke.sh [build_dir] [qemu_bin]
set -euo pipefail
S=/tmp/claude-1000/-home-tjocius/c9df6fee-712c-4f9d-b43d-1459f850eff8/scratchpad
BUILD=${1:-$S/fwbuild/.pio/build/esp32-s3-devkitc-1}
QEMU=${2:-$S/qemu/qemu/bin/qemu-system-xtensa}
OUT=${OUT:-$S/qemu_smoke}
mkdir -p "$OUT"
BOOT_APP0=$(ls "$HOME"/.platformio/packages/framework-arduinoespressif32/tools/partitions/boot_app0.bin)
uvx esptool --chip esp32s3 merge-bin -o "$OUT/flash.bin" --flash-mode dio --flash-size 8MB --pad-to-size 8MB \
  0x0 "$BUILD/bootloader.bin" 0x8000 "$BUILD/partitions.bin" 0xe000 "$BOOT_APP0" 0x10000 "$BUILD/firmware.bin"
# Octal PSRAM model is needed because the devkit env is built with BOARD_HAS_PSRAM + qio_opi
timeout 25 "$QEMU" -nographic -machine esp32s3 \
  -drive file="$OUT/flash.bin",if=mtd,format=raw \
  -global driver=ssi_psram,property=is_octal,value=true \
  -serial mon:stdio > "$OUT/serial.log" 2>&1 || true
echo "serial log: $OUT/serial.log"
head -c 3000 "$OUT/serial.log"
