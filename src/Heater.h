#ifndef ___MR_BOX_PERIPHERAL_BOARD__HEATER__H___
#define ___MR_BOX_PERIPHERAL_BOARD__HEATER__H___

#include <Arduino.h>
#include <stdint.h>
#include "pins.h"

namespace mr_box_peripheral_board {

class Heater {
public:

    void Heater_begin() {
        // Set Mosfet pins to output.
        pinMode(PIN_MOSFET_1, OUTPUT);
        pinMode(PIN_MOSFET_2, OUTPUT);

        digitalWrite(PIN_MOSFET_1, LOW);
        digitalWrite(PIN_MOSFET_2, LOW);
    }

};

}  // namespace mr_box_peripheral_board {

#endif  // #ifndef ___MR_BOX_PERIPHERAL_BOARD__PMT__H___
