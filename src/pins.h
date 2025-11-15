/*
Pin Number 	Port Pin Name	    Arduino Digital Pin
2	        PD0	                RX (Serial communication)
3	        PD1	                TX (Serial communication)
4-7	        PD2-PD5	            Digital Pins 2-5
8-12	    PB0-PB4	            Digital Pins 8-12
14-17	    PB5-PB7	            Digital Pins 13-15 (PB5 is also SCK)
23-28	    PC0-PC5	            Analog Inputs A0-A5 (also Digital Pins 16-21)
29-32	    PD6, PD7, PB6, PB7	Digital Pins 6, 7, 22, 23 (Note: PB6/PB7 also XTAL)
*/

#ifndef ___MR_BOX_PERIPHERAL_BOARD__PINS__H___
#define ___MR_BOX_PERIPHERAL_BOARD__PINS__H___

#include <Arduino.h>

// Stepper pins
const uint8_t PIN_MICRO_STEPPING_1 = 8; // PB0
const uint8_t PIN_MICRO_STEPPING_2 = 9; // PB1
const uint8_t PIN_MICRO_STEPPING_3 = 7; // PD7
const uint8_t PIN_STEP = 6; // PD6
const uint8_t PIN_DIRECTION = 5; // PD5
const uint8_t PIN_ENABLE = 2; // PD2

// Endstops
const uint8_t PIN_END_STOP_1 = 14;  // PC0
const uint8_t PIN_END_STOP_2 = 15;  // PC1

// MOSFETs
const uint8_t PIN_MOSFET_1 = 4;   // PD4
const uint8_t PIN_MOSFET_2 = 10;  // PB2

// Thermistors
const uint8_t PIN_THERMISTOR_1 = A3;   // PC3
const uint8_t PIN_THERMISTOR_2 = A2;   // PC2

// Communication
const uint8_t PIN_SDA = 18;   // PC4
const uint8_t PIN_SCL = 19;   // PC5
const uint8_t PIN_MOSI = 11;   // PB3
const uint8_t PIN_MISO = 12;   // PB4
const uint8_t PIN_SCK = 13;   // PB5

// Other
const uint8_t PIN_GPIO = 3;   // PD3
const uint8_t PIN_ADC2 = A2;   // PC2
const uint8_t PIN_ADC3 = A3;   // PC3
const uint8_t PIN_ADC6 = A6;   // ADC6
const uint8_t PIN_ADC7 = A7;   // ADC7

#endif  // #ifndef ___MR_BOX_PERIPHERAL_BOARD__PINS__H___