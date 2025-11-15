#ifndef ___BASE_NODE_RPC__Z_STAGE__H___
#define ___BASE_NODE_RPC__Z_STAGE__H___

#include <Arduino.h>
#include <stdint.h>
#include <time.h>
#include "pins.h"

namespace base_node_rpc {


class ZStage {
private:
  // Consider analog values less than a quarter of full 10-bit range as `LOW`.
  const int ANALOG_LOW_THRESHOLD = 1024 / 4;

  class ZStageState {
    public:
        float position;
        bool motor_enabled;
        bool micro_stepping;
        uint32_t RPM;
        bool home_stop_enabled;
        bool engaged_stop_enabled;
  };

  ZStageState state_;

public:
  ZStage() {
    pinMode(PIN_MICRO_STEPPING_1, OUTPUT);
    pinMode(PIN_MICRO_STEPPING_2, OUTPUT);
    pinMode(PIN_MICRO_STEPPING_3, OUTPUT);
    pinMode(PIN_STEP, OUTPUT);
    pinMode(PIN_DIRECTION, OUTPUT);
    pinMode(PIN_ENABLE, OUTPUT);

    digitalWrite(PIN_DIRECTION, LOW);

    pinMode(PIN_END_STOP_1, INPUT_PULLUP);

    _zstage_reset();
  }

  /****************************************************************************
   * Mutators */
  void _zstage_reset() {
    state_.position = 0;
    _zstage_disable_motor();
    _zstage_enable_micro_stepping();
    state_.RPM = 50;
    state_.home_stop_enabled = true;
    state_.engaged_stop_enabled = false;
  }

  void _zstage_home() {
    if (!state_.home_stop_enabled) return;

    // Use timer to stop homing if it gets stuck
    time_t start = time(0);
    int timeLeft = 10; // Timeout after 10 seconds

    state_.position = 100;
    while (!_zstage_at_home() && (timeLeft > 0)) {
      // Update timer
      time_t end = time(0);
      time_t timeTaken = end - start;
      timeLeft = 10 - timeTaken;

      _zstage_move(1., 25., false);
    }
    state_.position = 0;
  }

  void _zstage_move_to(float new_position) {
    float distance = new_position - state_.position;
    bool direction = true;

    if (distance < 0) {
      direction = 0;
      distance = -distance;
    }
    _zstage_move(distance, state_.RPM, direction);
  }

  float _zstage_move(float distance, int RPM, bool direction) {
    /*
     * Parameters
     * ----------
     * distance
     *   Distance (in mm)
     * RPM
     * direction
     *   Direction (true -> Up)
     *
     * Returns
     * -------
     * float
     *     New position.
     */
     // check if the motor is enabled when the function was called
    bool prev_motor_enabled = _zstage_motor_enabled();
    // enable it before moving if necessary
    if (!_zstage_motor_enabled()) _zstage_enable_motor();

    float position = state_.position;
    if (direction){
      digitalWrite(PIN_DIRECTION, HIGH);
      position += distance;
    } else{
      digitalWrite(PIN_DIRECTION, LOW);
      position -= distance;
    }

    if (position < 0){
      distance += position;
      position = 0;
    }

    uint8_t micro_stepping = state_.micro_stepping ? 16 : 1;
    int pulse = 150000 / RPM / float(micro_stepping);
    float steps = distance * 25. * float(micro_stepping);

    for (int x = 0; x < steps; x++) {
      digitalWrite(PIN_STEP, HIGH);
      delayMicroseconds(pulse);
      digitalWrite(PIN_STEP, LOW);
      delayMicroseconds(pulse);
    }

    state_.position = position;

    // if the motor was initially disabled, disable it again
    if (!prev_motor_enabled) _zstage_disable_motor();
    return position;
  }

  /*************************************************************
   * Setters */
  bool _zstage_set_position(float position) {
    state_.position = position;
    _zstage_move_to(position);
    return true;
  }

  void _zstage_set_RPM(uint32_t RPM) { state_.RPM = RPM; }

  void _zstage_enable_motor() {
    state_.motor_enabled = true;
    digitalWrite(PIN_ENABLE, LOW);  // Enable
  }

  void _zstage_disable_motor() {
    state_.motor_enabled = false;
    digitalWrite(PIN_ENABLE, HIGH);  // Disable
  }

  void _zstage_enable_micro_stepping() {
    state_.micro_stepping = true;
    digitalWrite(PIN_MICRO_STEPPING_1, HIGH);
    digitalWrite(PIN_MICRO_STEPPING_2, HIGH);
    digitalWrite(PIN_MICRO_STEPPING_3, HIGH);
  }

  void _zstage_disable_micro_stepping() {
    state_.micro_stepping = false;
    digitalWrite(PIN_MICRO_STEPPING_1, LOW);
    digitalWrite(PIN_MICRO_STEPPING_2, LOW);
    digitalWrite(PIN_MICRO_STEPPING_3, LOW);
  }

  /*************************************************************
   * Getters */
  void _zstage_enable_engaged_stop() { state_.engaged_stop_enabled = true; }
  void _zstage_disable_engaged_stop() { state_.engaged_stop_enabled = false; }
  void _zstage_enable_home_stop() { state_.home_stop_enabled = true; }
  void _zstage_disable_home_stop() { state_.home_stop_enabled = false; }

  float _zstage_position() const { return state_.position; }
  bool _zstage_motor_enabled() const { return state_.motor_enabled; }
  bool _zstage_micro_stepping() const { return state_.micro_stepping; }
  uint32_t _zstage_RPM() const { return state_.RPM; }
  bool _zstage_home_stop_enabled() const { return state_.home_stop_enabled; }
  bool _zstage_engaged_stop_enabled() const { return state_.engaged_stop_enabled; }

  bool _zstage_at_home() {
    return state_.home_stop_enabled && digitalRead(PIN_END_STOP_1);
  }

  /*bool _zstage_engaged() {
    // TODO: if state_.engaged_stop_enabled == false, check if the position
    // matches the config_._.zstage_up_position
    return state_.engaged_stop_enabled && (analogRead(PIN_END_STOP_2) <
                                           ANALOG_LOW_THRESHOLD);
  }*/
};

}  // namespace base_node_rpc {

#endif  // #ifndef ___BASE_NODE_RPC__Z_STAGE__H___
