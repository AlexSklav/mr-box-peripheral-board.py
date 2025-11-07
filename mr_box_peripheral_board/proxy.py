import time
import threading

import numpy as np
import pandas as pd

import base_node_rpc as bnr

from typing import Optional


from path_helpers import path
from nadamq.NadaMq import cPacket
from logging_helpers import _L
from base_node_rpc.proxy import ConfigMixinBase

from .bin.upload import upload

from ._version import get_versions

__version__ = get_versions()['version']
del get_versions

BOARD_BAUDRATE = 57600
DEVICE_NAME = 'mr-box-peripheral-board'

try:
    # XXX The `node` module containing the `Proxy` class definition is
    # generated from the `mr_box_peripheral_board::Node` class in
    # the C++ file `src/Node.hpp`.
    from .node import (Proxy, I2cProxy as _I2cProxy)
    # XXX The `config` module containing the `Config` class definition is
    # generated from the Protocol Buffer file `src/mr_box_config.proto`.
    from .mr_box_config import MrBoxConfig as Config


    class ConfigMixin(ConfigMixinBase):
        @property
        def config_class(self):
            return Config


    class ProxyMixin(ConfigMixin):
        """
        Mixin class to add convenience wrappers around methods of the generated
        `node.Proxy` class.
        """
        host_package_name = str(path(__file__).parent.name.replace('_', '-'))

        def __init__(self, *args, **kwargs):
            self.transaction_lock = threading.RLock()

            try:
                super().__init__(*args, **kwargs)

                ignore = kwargs.pop('ignore', [])
                self.zstage = self.ZStage(self)
                # self.led1 = self.LED(self, 5)
                # self.led2 = self.LED(self, 6)

                self.signals.signal('connected').send({'event': 'connected'})
            except Exception:
                _L().debug('Error connecting to device.', exc_info=True)
                self.terminate()
                raise

        @property
        def signals(self):
            """
            Version log
            -----------
            .. versionadded:: 1.43
            """
            return self._packet_queue_manager.signals

        def __del__(self) -> None:
            try:
                super().__del__()
            except Exception:
                # ignore any exceptions (e.g., if we can't communicate with the board)
                _L().debug('Communication error', exc_info=True)
                pass

        def get_adc_calibration(self):
            calibration_settings = \
            pd.Series({'Self-Calibration_Gain': self.MAX11210_getSelfCalGain(),
                       'Self-Calibration_Offset': self.MAX11210_getSelfCalOffset(),
                       'System_Gain': self.MAX11210_getSysGainCal(),
                       'System_Offset': self.MAX11210_getSysOffsetCal()})
            return calibration_settings

        class LED(object):
            def __init__(self, parent, pin):
                self._parent = parent
                self._pin = pin
                self._brightness = 0
                self._on = False

                # initialize brightness to 10%
                self.on = False
                self.brightness = 0.1

                # set LED pin as an output
                parent.pin_mode(pin, 1)

            @property
            def brightness(self):
                return self._brightness

            @brightness.setter
            def brightness(self, value):
                if 0 <= value <= 1:
                    self._brightness = value
                else:
                    raise ValueError('Value must be between 0 and 1.')
                if self._on:
                    self._parent.analog_write(self._pin,
                                              self._brightness * 255.0)

            @property
            def on(self):
                return self._on

            @on.setter
            def on(self, value):
                if value:
                    brightness = self._brightness
                else:
                    brightness = 0
                self._on = value
                self._parent.analog_write(self._pin, brightness * 255.0)

        class ZStage(object):
            def __init__(self, parent):
                self._parent = parent

            @property
            def position(self):
                return self._parent._zstage_position()

            @position.setter
            def position(self, value):
                """
                Move z-stage to specified position.

                **Note** Unlike the other properties, this does not directly
                modify the member variable on the device.  Instead, this relies
                on the ``position`` variable being updated by the device once
                the actual movement is complete.
                """
                self.move_to(value)

            @property
            def motor_enabled(self):
                return self._parent._zstage_motor_enabled()

            @motor_enabled.setter
            def motor_enabled(self, value):
                self.update_state(motor_enabled=value)

            @property
            def micro_stepping(self):
                return self._parent._zstage_micro_stepping()

            @micro_stepping.setter
            def micro_stepping(self, value):
                self.update_state(micro_stepping=value)

            @property
            def RPM(self):
                return self._parent._zstage_RPM()

            @RPM.setter
            def RPM(self, value):
                self.update_state(RPM=value)

            @property
            def home_stop_enabled(self):
                return self._parent._zstage_home_stop_enabled()

            @home_stop_enabled.setter
            def home_stop_enabled(self, value):
                self.update_state(home_stop_enabled=value)

            @property
            def is_up(self):
                # TODO: if the engaged_stop is enabled, use it
                # This functionality could also be pushed into the firmware
                return (self.state['position'] ==
                        self._parent.config['zstage_up_position'])

            def up(self):
                if not self.is_up:
                    self._parent._zstage_move_to(
                        self._parent.config['zstage_up_position'])
                    time.sleep(1)

            @property
            def is_down(self):
                return (self.state['position'] ==
                        self._parent.config['zstage_down_position'])

            def down(self):
                if not self.is_down:
                    self._parent._zstage_move_to(
                        self._parent.config['zstage_down_position'])

            def home(self):
                self._parent._zstage_home()

            @property
            def engaged_stop_enabled(self):
                return self._parent._zstage_engaged_stop_enabled()

            @engaged_stop_enabled.setter
            def engaged_stop_enabled(self, value):
                self.update_state(engaged_stop_enabled=value)

            @property
            def state(self):
                state = {'engaged_stop_enabled':self._parent._zstage_engaged_stop_enabled(),
                         'home_stop_enabled': self._parent._zstage_home_stop_enabled(),
                         'micro_stepping': self._parent._zstage_micro_stepping(),
                         'motor_enabled': self._parent._zstage_motor_enabled(),
                         'position': self._parent._zstage_position(),
                         'RPM': self._parent._zstage_RPM()}
                return pd.Series(state, dtype=object)

            def update_state(self, **kwargs):
                bool_fields = ('engaged_stop_enabled', 'home_stop_enabled',
                            'motor_enabled', 'micro_stepping')
                for key_i, value_i in kwargs.items():
                    if key_i in bool_fields:
                        action = 'enable' if value_i else 'disable'
                        getattr(self._parent, '_zstage_{action}_{0}'
                                .format(key_i.replace('_enabled', ''),
                                        action=action))()
                    else:
                        getattr(self._parent,
                                '_zstage_set_{0}'.format(key_i))(value_i)

            def move_to(self, position):
                self._parent._zstage_move_to(position)

        def close(self):
            self.terminate()

        @property
        def id(self):
            return self.config['id']

        @id.setter
        def id(self, id):
            return self.set_id(id)

        def _hardware_version(self) -> np.array:
            return super(ProxyMixin, self).hardware_version()

        @property
        def hardware_version(self) -> str:
            return self._hardware_version().tobytes().decode('utf-8')

        def _connect(self, *args, **kwargs) -> None:
            """
            Version log
            -----------
            .. versionadded:: 1.55

                Send ``connected`` event each time a connection has been
                established. Note that the first ``connected`` event is sent
                before any receivers have a chance to connect to the signal,
                but subsequent restored connection events after connecting to
                the ``connected`` signal will be received.
            """
            super(ProxyMixin, self)._connect(*args, **kwargs)
            self.signals.signal('connected').send({'event': 'connected'})

    class I2cProxy(ProxyMixin, _I2cProxy):
        pass

    class SerialProxy(ProxyMixin, Proxy):
        device_name = DEVICE_NAME
        device_version = __version__

        def __init__(self, settling_time_s: Optional[float] = 2.5,
                     baudrate: Optional[int] = BOARD_BAUDRATE, **kwargs):
            """
            Parameters
            ----------
            settling_time_s: float, optional
                If specified, wait :data:`settling_time_s` seconds after
                establishing serial connection before trying to execute test
                command.

                By default, :data:`settling_time_s` is set to 50 ms.
            baudrate: int, optional
                Baud rate for serial communication. Default is 57600.
            **kwargs
                Extra keyword arguments to pass on to
                :class:`base_node_rpc.proxy.SerialProxyMixin`.

            Version log
            -----------
            . versionchanged:: 1.40
                Delegate automatic port selection to
                :class:`base_node_rpc.proxy.SerialProxyMixin`.
            """
            self.default_timeout = kwargs.pop('timeout', 5)
            self.monitor = None
            port = kwargs.pop('port', None)

            # kwargs['settling_time_s'] = self.settling_time_s
            kwargs['baudrate'] = baudrate
            kwargs['device_name'] = self.device_name
            kwargs['device_version'] = self.device_version

            if port is None:
                # Find Boards by default when screening port we skip UART
                # So we need to pass skip_descriptor as None to find the board
                df_devices = bnr.available_devices(timeout=self.default_timeout,
                                                   baudrate=baudrate,
                                                   settling_time_s=settling_time_s,
                                                   skip_descriptor=None)
                if not df_devices.shape[0]:
                    raise IOError('No serial devices available for connection')
                df_boards = df_devices.loc[df_devices.device_name == self.device_name]
                if not df_boards.shape[0]:
                    raise IOError('No peripheral board available for connection')
                port = df_boards.index[0]

            self.connect(port, baudrate)
            super(SerialProxy, self).__init__(**kwargs)

        @property
        def signals(self):
            return self.monitor.signals

        def connect(self, port=None, baudrate=BOARD_BAUDRATE):
            self.terminate()
            monitor = bnr.ser_async.BaseNodeSerialMonitor(port=port,
                                                          baudrate=baudrate)
            monitor.start()
            monitor.connected_event.wait()
            self.monitor = monitor
            return self.monitor

        def _send_command(self, packet: cPacket, timeout_s: Optional[float] = None, **kwargs):
            if timeout_s is None:
                timeout_s = self.default_timeout
            _L().debug(f'Using timeout {timeout_s}')
            return self.monitor.request(packet.tobytes(), timeout=timeout_s)

        def terminate(self) -> None:
            if self.monitor is not None:
                self.monitor.stop()

        def __enter__(self) -> 'SerialProxy':
            return self

        def __exit__(self, exc_type, exc_value, traceback) -> None:
            self.terminate()

        def __del__(self) -> None:
            self.terminate()


        def flash_firmware(self) -> None:
            # currently, we're ignoring the hardware version, but eventually,
            # we will want to pass it to upload()
            self.terminate()
            try:
                upload()
            except Exception:
                _L().debug('Error updating firmware', exc_info=True)
            time.sleep(0.5)
            self.connect()

except (ImportError, TypeError):
    Proxy = None
    I2cProxy = None
    SerialProxy = None
