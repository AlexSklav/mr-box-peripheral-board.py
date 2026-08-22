import contextlib
import logging
import math
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
    # generated from the Protocol Buffer file `src/mrbox_config.proto`.
    from .mrbox_config import MrboxConfig as Config


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

                self.zstage = self.ZStage(self)
                # self.led1 = self.LED(self, 5)
                # self.led2 = self.LED(self, 6)

                self.signals.signal('connected').send({'event': 'connected'})
            except Exception:
                _L().debug('Error connecting to device.', exc_info=True)
                # N.B. `terminate()` is only defined by `SerialProxy`; the
                # `I2cProxy` flavour of this mixin has no such method.  Guard
                # the call so that a missing `terminate()` cannot mask the
                # original connection error with an `AttributeError`.
                if hasattr(self, 'terminate'):
                    try:
                        self.terminate()
                    except Exception:
                        _L().debug('Error terminating connection.',
                                   exc_info=True)
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
            """
            Release the serial port when the proxy is garbage collected.

            **Note** There is no ``__del__`` anywhere in the ``SerialProxy``
            MRO (``ProxyMixin`` -> ``ConfigMixin`` -> ``ConfigMixinBase`` ->
            ``node.Proxy`` -> ``ProxyBase``), so the previous
            ``super().__del__()`` call raised (and swallowed) an
            ``AttributeError``, leaving the port held open until process exit.
            Call :meth:`terminate` instead, guarded because ``I2cProxy`` does
            not define one.
            """
            try:
                terminate = getattr(self, 'terminate', None)
                if terminate is not None:
                    terminate()
            except Exception:
                # ignore any exceptions (e.g., if we can't communicate with
                # the board, or interpreter shutdown has torn down globals)
                _L().debug('Communication error', exc_info=True)

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
            # Serial timeout (in seconds) used for blocking z-stage motion
            # commands (i.e., `home` and `move_to`).
            #
            # XXX The firmware implementations of `_zstage_home()` and
            # `_zstage_move_to()` block until the motion completes, which may
            # take up to ~10 seconds (the firmware caps homing at 10 s).  If
            # the serial timeout is shorter than the firmware call, the
            # `base_node_rpc` serial monitor silently **re-sends** the command;
            # the response to the duplicate request later desynchronizes the
            # shared (uncorrelated) response queue, causing a *subsequent*
            # command to pop an empty/stale response.
            MOTION_TIMEOUT_S = 15

            # Absolute tolerance (in mm) used when comparing the reported
            # z-stage position against a configured target position.
            #
            # XXX The firmware reports the position as a 32-bit float, so an
            # exact `==` comparison against the (Python `float`) configuration
            # value is not reliable.
            POSITION_TOLERANCE_MM = 1e-2

            def __init__(self, parent):
                self._parent = parent
                # XXX `_moving` is a single shared flag, so it cannot describe
                # overlapping/concurrent moves: if a second non-blocking move
                # is started before the first completes, whichever move
                # finishes first clears the flag for both.  Callers are
                # expected to issue one motion command at a time.
                self._moving = False

            def _start_background(self, target, *args):
                """
                Run a motion command in a daemon thread.

                **Note** :attr:`_moving` is set *before* the thread is started
                so that a caller reading ``zstage.moving`` immediately after,
                e.g., ``up(blocking=False)`` observes ``True`` rather than
                racing the thread startup.
                """
                self._moving = True
                thread = threading.Thread(target=self._run_background,
                                          args=(target,) + args, daemon=True)
                thread.start()
                return thread

            def _run_background(self, target, *args):
                """
                Invoke ``target``, logging (rather than silently dropping) any
                exception raised in the daemon thread.
                """
                try:
                    target(*args)
                except Exception:
                    _L().warning('Error during non-blocking z-stage motion.',
                                 exc_info=True)

            @contextlib.contextmanager
            def _long_timeout(self, timeout_s):
                """
                Temporarily override the parent proxy serial timeout.

                **Note** The `default_timeout` attribute is shared instance
                state and non-blocking moves run in daemon threads, so hold
                the parent transaction lock for the duration of the override
                to prevent concurrent commands from observing (or clobbering)
                the temporary value.
                """
                with self._parent.transaction_lock:
                    original_timeout = self._parent.default_timeout
                    self._parent.default_timeout = timeout_s
                    try:
                        yield
                    finally:
                        self._parent.default_timeout = original_timeout

            @property
            def moving(self):
                return self._moving

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

            def _at_position(self, target):
                """
                Return ``True`` if the current position matches ``target``
                within :data:`POSITION_TOLERANCE_MM`.

                **Note** Reads :attr:`position` (a single RPC call) rather
                than :attr:`state` (six RPC calls).
                """
                return math.isclose(self.position, target,
                                    abs_tol=self.POSITION_TOLERANCE_MM)

            @property
            def is_up(self):
                # TODO: if the engaged_stop is enabled, use it
                # This functionality could also be pushed into the firmware
                return self._at_position(
                    self._parent.config['zstage_up_position'])

            def up(self, blocking=True):
                if blocking:
                    self._do_up()
                else:
                    self._start_background(self._do_up)

            def _do_up(self):
                self._moving = True
                try:
                    if not self.is_up:
                        with self._long_timeout(self.MOTION_TIMEOUT_S):
                            self._parent._zstage_move_to(
                                self._parent.config['zstage_up_position'])
                finally:
                    # N.B. clear the flag even if the move failed, otherwise a
                    # single error would leave the stage permanently reported
                    # as `moving`.
                    self._moving = False
                self._send_signals('up')

            @property
            def is_down(self):
                return self._at_position(
                    self._parent.config['zstage_down_position'])

            def down(self, blocking=True):
                if blocking:
                    self._do_down()
                else:
                    self._start_background(self._do_down)

            def _do_down(self):
                self._moving = True
                try:
                    if not self.is_down:
                        with self._long_timeout(self.MOTION_TIMEOUT_S):
                            self._parent._zstage_move_to(
                                self._parent.config['zstage_down_position'])
                finally:
                    self._moving = False
                self._send_signals('down')

            def home(self, blocking=True):
                if blocking:
                    self._do_home()
                else:
                    self._start_background(self._do_home)

            def _do_home(self):
                self._moving = True
                try:
                    with self._long_timeout(self.MOTION_TIMEOUT_S):
                        self._parent._zstage_home()
                finally:
                    self._moving = False
                self._send_signals('home')

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

            def move_to(self, position, blocking=True):
                if blocking:
                    self._do_move_to(position)
                else:
                    self._start_background(self._do_move_to, position)

            def _do_move_to(self, position):
                self._moving = True
                try:
                    with self._long_timeout(self.MOTION_TIMEOUT_S):
                        self._parent._zstage_move_to(position)
                finally:
                    self._moving = False
                self._send_signals('move_to')

            def _send_signals(self, label):
                """Send magnet and position signals after a move completes."""
                pos = self._parent._zstage_position()
                self._parent.signals.signal('magnet').send(
                    {'event': 'magnet', 'location': label, 'abs_position': pos})
        def close(self):
            self.terminate()

        @property
        def id(self):
            return self.config['id']

        @id.setter
        def id(self, id):
            return self.set_id(id)

        def _hardware_version(self) -> np.ndarray:
            return super().hardware_version()

        @property
        def hardware_version(self) -> str:
            # N.B. the firmware returns a fixed-size, NUL-padded buffer; strip
            # the trailing NULs before decoding (same as `dropbot.py` does).
            return (self._hardware_version().tobytes().split(b'\0', 1)[0]
                    .decode('utf-8'))

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
            # Port actually used for the connection.  Recorded here (as well
            # as in `connect()`) so that, e.g., `flash_firmware()` can
            # reconnect to the *same* port rather than re-running discovery.
            # N.B. subclasses (e.g., Microdrop's `DramatiqPeripheralSerialProxy`)
            # override `connect()` without necessarily setting `self.port`, so
            # this assignment is the authoritative fallback.
            self.port = None
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
                                                   skip_manufacturer=None)
                if not df_devices.shape[0]:
                    raise IOError('No serial devices available for connection')
                df_boards = df_devices.loc[df_devices.device_name == self.device_name]
                if not df_boards.shape[0]:
                    raise IOError('No peripheral board available for connection')
                port = df_boards.index[0]

            self.port = port
            self.connect(port, baudrate)
            super().__init__(**kwargs)

        @property
        def signals(self):
            return self.monitor.signals

        def connect(self, port=None, baudrate=BOARD_BAUDRATE,
                    settling_time_s: float = 0):
            """
            Parameters
            ----------
            port : str, optional
                Serial port to connect to.  If ``None``, the serial monitor
                performs its own device discovery.
            baudrate : int, optional
                Baud rate for serial communication.
            settling_time_s : float, optional
                Number of seconds to wait after the connection has been
                established (e.g., to let a board that resets on connection
                finish booting) before returning.  Defaults to ``0``, i.e., no
                additional delay, which preserves the previous behaviour.
            """
            self.terminate()
            monitor = bnr.ser_async.BaseNodeSerialMonitor(port=port,
                                                          baudrate=baudrate)
            monitor.start()
            monitor.connected_event.wait()
            if settling_time_s and settling_time_s > 0:
                time.sleep(settling_time_s)
            self.port = port
            self.monitor = monitor
            return self.monitor

        def _send_command(self, packet: cPacket, timeout_s: Optional[float] = None, **kwargs):
            if timeout_s is None:
                timeout_s = self.default_timeout
            _L().debug(f'Using timeout {timeout_s}')
            # Serialize commands issued from different threads.  Without this,
            # a command issued while a long (e.g., z-stage motion) command is
            # in flight burns through its timeout/retry budget waiting behind
            # the other request in `BaseNodeSerialMonitor.arequest()` (which
            # holds an `asyncio.Lock` for the drain -> write -> get sequence),
            # surfacing as a spurious `TimeoutError`.  Blocking here instead
            # turns that into plain back-pressure.
            #
            # N.B. `transaction_lock` is an `RLock`, so a command issued from
            # within a `ZStage._long_timeout()` block (which holds the same
            # lock) does not deadlock.
            with self.transaction_lock:
                return self.monitor.request(packet.tobytes(),
                                            timeout=timeout_s)

        def terminate(self) -> None:
            if self.monitor is not None:
                self.monitor.stop()

        def __enter__(self) -> 'SerialProxy':
            return self

        def __exit__(self, exc_type, exc_value, traceback) -> None:
            self.terminate()

        def flash_firmware(self) -> None:
            # currently, we're ignoring the hardware version, but eventually,
            # we will want to pass it to upload()
            #
            # N.B. capture the port *before* tearing down the connection and
            # reconnect to that same port afterwards.  Reconnecting with
            # ``port=None`` makes the serial monitor keepalive block forever
            # waiting for a device it never opens.
            port = getattr(self, 'port', None)
            self.terminate()
            error = None
            try:
                upload()
            except Exception as e:
                _L().warning('Error updating firmware.', exc_info=True)
                error = e
            time.sleep(0.5)
            try:
                self.connect(port)
            except Exception:
                if error is not None:
                    _L().warning('Could not reconnect after failed firmware '
                                 'update.', exc_info=True)
                raise
            if error is not None:
                # Surface the failure to the caller; the connection has been
                # re-established at this point.
                raise IOError('Error updating firmware.') from error

except (ImportError, TypeError) as e:
    # The `node` and `mrbox_config` modules are generated at build time, so
    # they are legitimately missing during a fresh conda build.  Keep the
    # warning to a single (still diagnosable) line and reserve the traceback
    # for debug level.
    _logger = logging.getLogger(__name__)
    _logger.warning('Could not import generated `node`/`mrbox_config` modules '
                    f'from `{__package__}`: {e!r}')
    _logger.debug('Could not import generated `node`/`mrbox_config` modules '
                  f'from `{__package__}`', exc_info=True)
    Proxy = None
    I2cProxy = None
    SerialProxy = None
