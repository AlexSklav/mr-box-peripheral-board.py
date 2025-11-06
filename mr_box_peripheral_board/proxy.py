from __future__ import absolute_import
import logging
logger = logging.getLogger(__name__)

from typing import Optional
from collections import OrderedDict
import threading
import time
import pandas as pd


from base_node_rpc.proxy import ConfigMixinBase
from nadamq.NadaMq import cPacket
import base_node_rpc as bnr
from logging_helpers import _L

from ._version import get_versions

__version__ = get_versions()['version']
del get_versions

try:
    # XXX The `node` module containing the `Proxy` class definition is
    # generated from the `mr_box_peripheral_board::Node` class in
    # the C++ file `src/Node.hpp`.
    from .node import (Proxy as _Proxy, I2cProxy as _I2cProxy,
                       SerialProxy as _SerialProxy)
    # XXX The `config` module containing the `Config` class definition is
    # generated from the Protocol Buffer file `src/config.proto`.
    from .mr_box_config import MrBoxConfig as Config


    class ConfigMixin(ConfigMixinBase):
        @property
        def config_class(self):
            return Config


    class ProxyMixin(ConfigMixin):
        '''
        Mixin class to add convenience wrappers around methods of the generated
        `node.Proxy` class.
        '''
        host_package_name = 'mr-box-peripheral-board'

        def __init__(self, *args, **kwargs):
            self.transaction_lock = threading.RLock()

            try:
                super(ProxyMixin, self).__init__(*args, **kwargs)

                ignore = kwargs.pop('ignore', [])
                self.zstage = self.ZStage(self)

            except Exception:
                logger.debug('Error connecting to device.', exc_info=True)
                self.terminate()
                raise

        @property
        def signals(self):
            return self._packet_queue_manager.signals

        def get_adc_calibration(self):
            calibration_settings = \
            pd.Series(OrderedDict([('Self-Calibration_Gain', self.MAX11210_getSelfCalGain()),
                                   ('Self-Calibration_Offset', self.MAX11210_getSelfCalOffset()),
                                   ('System_Gain', self.MAX11210_getSysGainCal()),
                                   ('System_Offset', self.MAX11210_getSysOffsetCal())]))
            return calibration_settings

        class ZStage(object):
            def __init__(self, parent):
                self._parent = parent

            @property
            def position(self):
                return self._parent._zstage_position()

            @position.setter
            def position(self, value):
                '''
                Move z-stage to specified position.

                **Note** Unlike the other properties, this does not directly
                modify the member variable on the device.  Instead, this relies
                on the ``position`` variable being updated by the device once
                the actual movement is complete.
                '''
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
                state = OrderedDict([('engaged_stop_enabled',
                                      self._parent._zstage_engaged_stop_enabled()),
                                     ('home_stop_enabled',
                                      self._parent._zstage_home_stop_enabled()),
                                     ('micro_stepping',
                                      self._parent._zstage_micro_stepping()),
                                     ('motor_enabled',
                                      self._parent._zstage_motor_enabled()),
                                     ('position', self._parent._zstage_position()),
                                     ('RPM', self._parent._zstage_RPM())])
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


    class I2cProxy(ProxyMixin, _I2cProxy):
        pass


    class SerialProxy(ProxyMixin, _Proxy):
        device_name = 'mr-box-peripheral-board'
        device_version = __version__

        def __init__(self, settling_time_s: Optional[float] = 2.5, **kwargs):
            """
            Parameters
            ----------
            settling_time_s: float, optional
                If specified, wait :data:`settling_time_s` seconds after
                establishing serial connection before trying to execute test
                command.

                By default, :data:`settling_time_s` is set to 50 ms.
            **kwargs
                Extra keyword arguments to pass on to
                :class:`base_node_rpc.proxy.SerialProxyMixin`.

            Version log
            -----------
            . versionchanged:: 1.40
                Delegate automatic port selection to
                :class:`base_node_rpc.proxy.SerialProxyMixin`.
            """
            self.baudrate = 57600
            self.default_timeout = kwargs.pop('timeout', 5)
            self.monitor = None
            kwargs['baudrate'] = self.baudrate
            kwargs['device_name'] = self.device_name
            kwargs['device_version'] = self.device_version

            port = kwargs.pop('port', None)
            if port is None:
                # Find devices
                df_devices = bnr.available_devices(baudrate=kwargs['baudrate'], timeout=self.default_timeout,
                                                   settling_time_s=settling_time_s)
                if not df_devices.shape[0]:
                    raise IOError('No serial devices available for connection')
                df_mr_box = df_devices.loc[df_devices.device_name == self.device_name]
                if not df_mr_box.shape[0]:
                    raise IOError('No Device available for connection')
                port = df_mr_box.index[0]

            self.port = port
            self.connect()
            super(SerialProxy, self).__init__(**kwargs)

        @property
        def signals(self):
            return self.monitor.signals

        def connect(self):
            self.terminate()
            monitor = bnr.ser_async.BaseNodeSerialMonitor(port=self.port, baudrate=self.baudrate)
            monitor.start()
            monitor.connected_event.wait()
            self.monitor = monitor
            return self.monitor

        def _send_command(self, packet: cPacket, timeout_s: Optional[float] = None, **kwargs):
            if timeout_s is None:
                timeout_s = self.default_timeout
            _L().debug(f'Using timeout {timeout_s}')
            return self.monitor.request(packet.tostring(), timeout=timeout_s)

        def terminate(self) -> None:
            if self.monitor is not None:
                self.monitor.stop()

        def __enter__(self) -> 'SerialProxy':
            return self

        def __exit__(self, exc_type, exc_value, traceback) -> None:
            self.terminate()

        def __del__(self) -> None:
            self.terminate()


except (ImportError, TypeError):
    I2cProxy = None
    SerialProxy = None
