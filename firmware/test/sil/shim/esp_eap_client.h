#pragma once
#include <cstdint>
typedef int esp_err_t;
esp_err_t esp_eap_client_set_identity(const unsigned char *v, int n);
esp_err_t esp_eap_client_set_username(const unsigned char *v, int n);
esp_err_t esp_eap_client_set_password(const unsigned char *v, int n);
esp_err_t esp_wifi_sta_enterprise_enable();
