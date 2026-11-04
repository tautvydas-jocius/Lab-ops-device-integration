#pragma once
#include "Arduino.h"
class USBClass { public: bool begin() { return true; } };
extern USBClass USB;
