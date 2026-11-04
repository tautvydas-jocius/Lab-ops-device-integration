# Software-in-the-loop: the real firmware sources (src/main.cpp, src/usb_drop.h) built for Linux against test/sil/shim.
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends g++ git ca-certificates libmbedtls-dev mtools dosfstools && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 -b v2.8 https://github.com/knolleary/pubsubclient /libs/pubsubclient \
 && git clone --depth 1 -b v7.4.3 https://github.com/bblanchon/ArduinoJson /libs/ArduinoJson
WORKDIR /fw
COPY src ./src
COPY test/sil ./sil
ENV FLAGS="-std=gnu++17 -O1 -g0 -w -DARDUINO=10819 -DESP32 -DARDUINOJSON_ENABLE_ARDUINO_STREAM=0 -DARDUINOJSON_ENABLE_ARDUINO_PRINT=0 -DARDUINOJSON_ENABLE_PROGMEM=0 -DARDUINOJSON_ENABLE_ARDUINO_STRING=1 -DUART_RX_PIN=1 -DUART_TX_PIN=2 -Isil -Isil/shim -I/libs/pubsubclient/src -I/libs/ArduinoJson/src -Isrc"
RUN mkdir -p /app && g++ $FLAGS -c sil/shim/sil.cpp -o /tmp/sil.o && g++ $FLAGS -c /libs/pubsubclient/src/PubSubClient.cpp -o /tmp/psc.o \
 && for v in 1.0.0 2.0.0; do g++ $FLAGS -DFW_VERSION="\"$v\"" -c src/main.cpp -o /tmp/main.o && g++ /tmp/main.o /tmp/sil.o /tmp/psc.o -lmbedcrypto -lpthread -o /app/sil-$v && strip /app/sil-$v; done
# Negative control for simulator/sil_test.py: same firmware with the serial-interference fix removed, the test must catch it
RUN mkdir -p /tmp/mut && cp src/* /tmp/mut/ && sed -i 's/pumpSerial();//g' /tmp/mut/usb_drop.h && sed -i 's/Serial1.setRxBufferSize(2048); //' /tmp/mut/main.cpp \
 && g++ $FLAGS -DFW_VERSION='"1.0.0"' -c /tmp/mut/main.cpp -o /tmp/mut.o && g++ /tmp/mut.o /tmp/sil.o /tmp/psc.o -lmbedcrypto -lpthread -o /app/sil-nofix && strip /app/sil-nofix
VOLUME /state
CMD ["sh", "-c", "[ \"$SIL_WIPE\" = 1 ] && find /state -mindepth 1 -delete; exec /app/sil-${SIL_BIN:-1.0.0}"]
