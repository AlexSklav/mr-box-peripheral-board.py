"""Hardware-free regression tests for :mod:`mr_box_peripheral_board.proxy`.

Every test here runs entirely against stubs -- no serial port, no network and
no firmware is touched.  Each test docstring names the defect it guards
against.
"""
import importlib
import logging
import sys
import threading
import time

import pytest

import mr_box_peripheral_board.proxy as P


# Package prefix used by the import-fallback isolation fixture.
PACKAGE = 'mr_box_peripheral_board'


# --------------------------------------------------------------------------
# Stubs
# --------------------------------------------------------------------------
class SignalStub:
    """Stand-in for a ``blinker`` signal that records what was sent."""

    def __init__(self, name, sink):
        self.name = name
        self.sink = sink

    def send(self, payload):
        self.sink.append((self.name, payload))


class SignalsStub:
    """Stand-in for the proxy ``signals`` namespace."""

    def __init__(self):
        self.sent = []

    def signal(self, name):
        return SignalStub(name, self.sent)


class ParentStub:
    """Minimal stand-in for the ``ProxyMixin`` parent used by ``ZStage``.

    Records every "RPC" invocation in :attr:`calls` so that tests can assert
    on the *number* of round-trips a property performs.
    """

    def __init__(self, position=0.0, fail=False, block_event=None):
        self.transaction_lock = threading.RLock()
        self.default_timeout = 5
        self.config = {'zstage_up_position': 23.0,
                       'zstage_down_position': 0.0}
        self.signals = SignalsStub()
        self._position = position
        self.fail = fail
        self.block_event = block_event
        self.calls = []
        self.timeouts_seen = []

    def _maybe_block_or_fail(self):
        self.timeouts_seen.append(self.default_timeout)
        if self.block_event is not None:
            assert self.block_event.wait(timeout=10), 'stub was never released'
        if self.fail:
            raise IOError('simulated motion failure')

    def _zstage_position(self):
        self.calls.append('position')
        return self._position

    def _zstage_move_to(self, position):
        self.calls.append(('move_to', position))
        self._maybe_block_or_fail()
        self._position = position

    def _zstage_home(self):
        self.calls.append('home')
        self._maybe_block_or_fail()
        self._position = 0.0


class MonitorStub:
    """Stand-in for ``BaseNodeSerialMonitor`` that tracks call concurrency."""

    def __init__(self, delay=0.0):
        self.delay = delay
        self._lock = threading.Lock()
        self._concurrent = 0
        self.max_concurrent = 0
        self.requests = []

    def request(self, data, timeout=None):
        with self._lock:
            self._concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self._concurrent)
        try:
            if self.delay:
                time.sleep(self.delay)
        finally:
            with self._lock:
                self._concurrent -= 1
                self.requests.append((data, timeout))
        return b'ok'

    def stop(self):
        pass


class PacketStub:
    """Stand-in for :class:`nadamq.NadaMq.cPacket`."""

    def tobytes(self):
        return b'\x01\x02'


def make_serial_proxy(monitor=None):
    """Build a ``SerialProxy`` without running its hardware ``__init__``."""
    proxy = object.__new__(P.SerialProxy)
    proxy.transaction_lock = threading.RLock()
    proxy.default_timeout = 5
    proxy.monitor = monitor if monitor is not None else MonitorStub()
    proxy.port = None
    return proxy


@pytest.fixture
def zstage_factory():
    """Return ``(zstage, parent)`` pairs built from :class:`ParentStub`."""
    def _factory(**kwargs):
        parent = ParentStub(**kwargs)
        return P.ProxyMixin.ZStage(parent), parent
    return _factory


# --------------------------------------------------------------------------
# 1. Moving-flag exception safety / ordering
# --------------------------------------------------------------------------
class TestMovingFlag:
    """``ZStage._moving`` bookkeeping."""

    @pytest.mark.parametrize('method_name', ['_do_home', '_do_up',
                                             '_do_move_to'])
    def test_moving_cleared_when_move_raises(self, zstage_factory,
                                             method_name):
        """Guards: a failed move used to leave ``_moving`` stuck at ``True``.

        The flag is now cleared from a ``try/finally``, so a single I/O error
        cannot leave the stage permanently reported as moving.
        """
        zstage, parent = zstage_factory(fail=True)
        args = (7.0,) if method_name == '_do_move_to' else ()

        with pytest.raises(IOError):
            getattr(zstage, method_name)(*args)

        assert zstage.moving is False
        # The success-only signal must not fire on the failure path.
        assert parent.signals.sent == []
        # The temporary motion timeout must have been restored.
        assert parent.default_timeout == 5

    def test_moving_flag_set_before_worker_thread_is_created(
            self, zstage_factory, monkeypatch):
        """Guards: ``_moving`` used to be set *inside* the worker thread.

        A caller reading ``zstage.moving`` immediately after
        ``up(blocking=False)`` then raced thread start-up and could observe
        ``False``.  Here ``threading.Thread`` is replaced by a recorder that
        never runs the target, so the flag can only be ``True`` if
        ``_start_background`` set it before constructing/starting the thread.
        """
        zstage, parent = zstage_factory()
        observed = {}

        class RecordingThread:
            def __init__(self, target=None, args=(), daemon=None, **kwargs):
                observed['at_construct'] = zstage.moving
                observed['daemon'] = daemon
                self.target = target
                self.args = args

            def start(self):
                observed['at_start'] = zstage.moving

        monkeypatch.setattr(P.threading, 'Thread', RecordingThread)
        zstage.up(blocking=False)

        assert observed['at_construct'] is True
        assert observed['at_start'] is True
        assert observed['daemon'] is True
        # The target never ran, proving the flag was not set by the worker.
        assert parent.calls == []
        assert zstage.moving is True

    def test_non_blocking_move_reports_moving_then_clears(self,
                                                          zstage_factory):
        """Guards: ``moving`` must be observable ``True`` for a real thread.

        End-to-end companion to the recorder test: the worker is held on an
        event, so the assertion cannot race the thread.
        """
        release = threading.Event()
        zstage, parent = zstage_factory(block_event=release)

        zstage.home(blocking=False)
        assert zstage.moving is True

        release.set()
        deadline = time.monotonic() + 10
        while zstage.moving and time.monotonic() < deadline:
            time.sleep(0.005)

        assert zstage.moving is False
        assert [name for name, _ in parent.signals.sent] == ['magnet']
        assert parent.signals.sent[0][1]['location'] == 'home'

    def test_background_failure_is_logged_not_swallowed(self, zstage_factory,
                                                        caplog):
        """Guards: exceptions in the daemon motion thread vanished silently."""
        zstage, _parent = zstage_factory(fail=True)

        with caplog.at_level(logging.WARNING):
            thread = zstage._start_background(zstage._do_home)
            thread.join(timeout=10)

        assert not thread.is_alive()
        assert zstage.moving is False
        warnings = [r for r in caplog.records
                    if r.levelno >= logging.WARNING
                    and 'non-blocking z-stage motion' in r.getMessage()]
        assert len(warnings) == 1
        assert warnings[0].exc_info is not None


# --------------------------------------------------------------------------
# 2. `_send_command` serialization under `transaction_lock`
# --------------------------------------------------------------------------
class TestSendCommandSerialization:
    """``SerialProxy._send_command`` holds ``transaction_lock``."""

    def test_concurrent_send_command_calls_are_serialized(self):
        """Guards: overlapping commands used to pile up inside the monitor.

        Without ``transaction_lock``, a command issued while a long z-stage
        motion was in flight burned its timeout budget queued behind the other
        request and surfaced as a spurious ``TimeoutError``.  Observed
        concurrency inside ``monitor.request`` must be exactly 1.
        """
        n_threads = 6
        delay = 0.02
        proxy = make_serial_proxy(MonitorStub(delay=delay))
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker():
            try:
                barrier.wait(timeout=10)
                proxy._send_command(PacketStub())
            except Exception as exception:  # pragma: no cover - diagnostics
                errors.append(exception)

        threads = [threading.Thread(target=worker, daemon=True)
                   for _ in range(n_threads)]
        start = time.monotonic()
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        elapsed = time.monotonic() - start

        assert errors == []
        assert all(not t.is_alive() for t in threads)
        assert proxy.monitor.max_concurrent == 1
        assert len(proxy.monitor.requests) == n_threads
        # Serialized execution cannot be faster than the sum of the delays.
        assert elapsed >= n_threads * delay * 0.9

    def test_send_command_uses_default_timeout(self):
        """Guards: an explicit ``timeout_s`` of ``None`` must fall back."""
        proxy = make_serial_proxy()
        proxy.default_timeout = 11

        proxy._send_command(PacketStub())
        proxy._send_command(PacketStub(), timeout_s=3)

        assert [timeout for _, timeout in proxy.monitor.requests] == [11, 3]


# --------------------------------------------------------------------------
# 3. `_long_timeout` nesting / re-entrancy
# --------------------------------------------------------------------------
class TestLongTimeout:
    """``ZStage._long_timeout`` holds the parent's re-entrant lock."""

    def test_nested_long_timeout_restores_previous_values(self,
                                                          zstage_factory):
        """Guards: nesting the override deadlocked on a plain ``Lock``.

        ``transaction_lock`` is an ``RLock``, so nested use must return and
        each level must restore the timeout it found.
        """
        zstage, parent = zstage_factory()
        result = []

        def nested():
            with zstage._long_timeout(15):
                result.append(parent.default_timeout)
                with zstage._long_timeout(30):
                    result.append(parent.default_timeout)
                result.append(parent.default_timeout)
            result.append(parent.default_timeout)

        thread = threading.Thread(target=nested, daemon=True)
        thread.start()
        thread.join(timeout=10)

        assert not thread.is_alive(), 'nested _long_timeout deadlocked'
        assert result == [15, 30, 15, 5]

    def test_send_command_inside_long_timeout_does_not_deadlock(self):
        """Guards: an RPC issued from inside the override self-deadlocked.

        ``_long_timeout`` holds ``transaction_lock`` for its whole body and
        ``_send_command`` acquires the same lock; only an ``RLock`` survives.
        """
        proxy = make_serial_proxy()
        zstage = P.ProxyMixin.ZStage(proxy)
        done = threading.Event()

        def nested():
            with zstage._long_timeout(15):
                proxy._send_command(PacketStub())
            done.set()

        thread = threading.Thread(target=nested, daemon=True)
        thread.start()
        thread.join(timeout=10)

        assert done.is_set(), '_send_command inside _long_timeout deadlocked'
        assert proxy.monitor.requests == [(b'\x01\x02', 15)]
        assert proxy.default_timeout == 5

    def test_long_timeout_restores_on_exception(self, zstage_factory):
        """Guards: an error inside the block left the long timeout in place."""
        zstage, parent = zstage_factory()

        with pytest.raises(ValueError):
            with zstage._long_timeout(15):
                assert parent.default_timeout == 15
                raise ValueError('boom')

        assert parent.default_timeout == 5

    def test_blocking_move_runs_under_motion_timeout(self, zstage_factory):
        """Guards: motion RPCs ran with the short default serial timeout.

        A firmware home can block for ~10 s; a shorter serial timeout made the
        monitor silently re-send and desynchronize the response queue.
        """
        zstage, parent = zstage_factory()

        zstage._do_home()

        assert parent.timeouts_seen == [
            P.ProxyMixin.ZStage.MOTION_TIMEOUT_S]
        assert parent.default_timeout == 5


# --------------------------------------------------------------------------
# 4. `is_up` / `is_down` tolerance and RPC economy
# --------------------------------------------------------------------------
class TestPositionTolerance:
    """``ZStage._at_position`` comparisons."""

    def test_is_up_issues_exactly_one_rpc(self, zstage_factory):
        """Guards: ``is_up`` read the full ``state`` (six RPCs) per query.

        It now reads ``position`` only, i.e. exactly one round-trip.
        """
        zstage, parent = zstage_factory(position=23.0)

        assert zstage.is_up is True
        assert parent.calls == ['position']

    def test_is_down_issues_exactly_one_rpc(self, zstage_factory):
        """Guards: ``is_down`` read the full ``state`` (six RPCs) per query."""
        zstage, parent = zstage_factory(position=0.0)

        assert zstage.is_down is True
        assert parent.calls == ['position']

    @pytest.mark.parametrize('position, expected', [
        (23.0, True),
        (23.0000001, True),   # exact `==` used to fail on the float32 readback
        (23.009, True),       # inside POSITION_TOLERANCE_MM
        (23.5, False),        # well outside the tolerance
        (0.0, False),
    ])
    def test_is_up_float_tolerance(self, zstage_factory, position, expected):
        """Guards: exact float equality against the configured target.

        The firmware reports a 32-bit float, so ``==`` against the Python
        ``float`` config value was unreliable; comparison now uses
        ``math.isclose`` with ``POSITION_TOLERANCE_MM``.
        """
        zstage, _parent = zstage_factory(position=position)
        assert zstage.is_up is expected

    @pytest.mark.parametrize('position, expected', [
        (0.0, True),
        (0.005, True),
        (0.5, False),
    ])
    def test_is_down_float_tolerance(self, zstage_factory, position,
                                     expected):
        """Guards: exact float equality against the down position."""
        zstage, _parent = zstage_factory(position=position)
        assert zstage.is_down is expected

    def test_exact_equality_would_have_regressed(self):
        """Witness for the original defect: ``23.0000001 == 23.0`` is False."""
        assert (23.0000001 == 23.0) is False
        assert P.ProxyMixin.ZStage.POSITION_TOLERANCE_MM == pytest.approx(1e-2)


# --------------------------------------------------------------------------
# 5. `flash_firmware` port bookkeeping and error propagation
# --------------------------------------------------------------------------
class TestFlashFirmware:
    """``SerialProxy.flash_firmware``."""

    @staticmethod
    def _make_stub_proxy(port):
        proxy = make_serial_proxy()
        proxy.port = port
        proxy.connect_calls = []
        proxy.terminate_calls = []
        proxy.terminate = lambda: proxy.terminate_calls.append(True)
        proxy.connect = (lambda *args, **kwargs:
                         proxy.connect_calls.append((args, kwargs)))
        return proxy

    @pytest.fixture(autouse=True)
    def _no_sleep(self, monkeypatch):
        """Skip the post-upload settling sleep to keep the suite fast."""
        monkeypatch.setattr(P.time, 'sleep', lambda seconds: None)

    def test_failed_upload_reconnects_to_recorded_port_and_reraises(
            self, monkeypatch, caplog):
        """Guards two defects at once.

        1. The upload failure was logged and then *swallowed*, so callers
           believed the flash had succeeded.
        2. The reconnect used ``port=None``, re-running discovery; the serial
           monitor keepalive then blocked forever on a device it never opened.
        """
        upload_calls = []

        def failing_upload():
            upload_calls.append(True)
            raise RuntimeError('avrdude failed')

        monkeypatch.setattr(P, 'upload', failing_upload)
        proxy = self._make_stub_proxy('/dev/ttyACM7')

        with caplog.at_level(logging.WARNING):
            with pytest.raises(IOError) as exc_info:
                proxy.flash_firmware()

        assert upload_calls == [True]
        # The original failure is chained rather than discarded.
        assert isinstance(exc_info.value.__cause__, RuntimeError)
        assert str(exc_info.value.__cause__) == 'avrdude failed'
        # Reconnect used the *recorded* port, not rediscovery.
        assert proxy.connect_calls == [(('/dev/ttyACM7',), {})]
        assert proxy.terminate_calls == [True]

        warnings = [r for r in caplog.records
                    if r.levelno >= logging.WARNING
                    and 'Error updating firmware' in r.getMessage()]
        assert len(warnings) == 1
        assert warnings[0].exc_info is not None

    def test_successful_upload_reconnects_and_does_not_raise(self,
                                                             monkeypatch):
        """Guards: the happy path must still reconnect to the same port."""
        upload_calls = []
        monkeypatch.setattr(P, 'upload',
                            lambda: upload_calls.append(True))
        proxy = self._make_stub_proxy('/dev/ttyACM3')

        proxy.flash_firmware()

        assert upload_calls == [True]
        assert proxy.connect_calls == [(('/dev/ttyACM3',), {})]

    def test_port_is_read_before_terminate(self, monkeypatch):
        """Guards: the port was read *after* teardown cleared it.

        ``terminate()`` here nulls ``self.port``; the recorded port must
        already have been captured, so the reconnect still names it.
        """
        monkeypatch.setattr(P, 'upload', lambda: None)
        proxy = make_serial_proxy()
        proxy.port = '/dev/ttyACM9'
        proxy.connect_calls = []

        def terminate():
            proxy.port = None

        proxy.terminate = terminate
        proxy.connect = (lambda *args, **kwargs:
                         proxy.connect_calls.append((args, kwargs)))

        proxy.flash_firmware()

        assert proxy.connect_calls == [(('/dev/ttyACM9',), {})]


# --------------------------------------------------------------------------
# 6. Optional-import fallback logging
# --------------------------------------------------------------------------
class _BlockGeneratedNode:
    """Meta-path finder making the generated ``node`` module unimportable."""

    target = f'{PACKAGE}.node'

    def find_spec(self, fullname, path=None, target=None):
        if fullname == self.target:
            raise ImportError(f'simulated missing module: {fullname}')
        return None


@pytest.fixture
def restore_package_imports():
    """Snapshot/restore ``sys.modules`` and ``sys.meta_path`` for the package.

    Lets a test re-import :mod:`mr_box_peripheral_board.proxy` under a broken
    import environment without leaking the crippled module into other tests.
    """
    saved_modules = {name: module for name, module in sys.modules.items()
                     if name == PACKAGE or name.startswith(PACKAGE + '.')}
    saved_meta_path = list(sys.meta_path)
    try:
        yield
    finally:
        sys.meta_path[:] = saved_meta_path
        for name in [n for n in list(sys.modules)
                     if n == PACKAGE or n.startswith(PACKAGE + '.')]:
            del sys.modules[name]
        sys.modules.update(saved_modules)
        # Re-attach submodules to their parent package objects, which the
        # failed re-import will have overwritten.
        for name, module in saved_modules.items():
            if '.' in name:
                parent_name, _, attribute = name.rpartition('.')
                parent = saved_modules.get(parent_name)
                if parent is not None:
                    setattr(parent, attribute, module)


class TestImportFallback:
    """The optional-import guard around the build-time generated modules."""

    def test_missing_generated_node_logs_and_does_not_raise(
            self, restore_package_imports, caplog, capsys):
        """Guards: a missing generated ``node`` module printed / re-raised.

        ``node`` and ``mrbox_config`` are generated at build time and are
        legitimately absent during a fresh conda build.  Importing the package
        must then degrade to ``Proxy = I2cProxy = SerialProxy = None`` after a
        single ``WARNING`` log record -- never a bare ``print`` and never an
        exception.
        """
        sys.meta_path.insert(0, _BlockGeneratedNode())
        for name in (f'{PACKAGE}.proxy', f'{PACKAGE}.node'):
            sys.modules.pop(name, None)

        with caplog.at_level(logging.WARNING):
            reloaded = importlib.import_module(f'{PACKAGE}.proxy')

        assert reloaded.Proxy is None
        assert reloaded.I2cProxy is None
        assert reloaded.SerialProxy is None

        warnings = [r for r in caplog.records
                    if r.levelno >= logging.WARNING
                    and r.name == f'{PACKAGE}.proxy']
        assert len(warnings) == 1, [r.getMessage() for r in caplog.records]
        message = warnings[0].getMessage()
        assert 'node' in message
        assert 'ImportError' in message

        # The fallback logs; it must not print to stdout/stderr.
        captured = capsys.readouterr()
        assert 'Could not import' not in captured.out
        assert 'Could not import' not in captured.err

    def test_package_imports_cleanly_when_generated_modules_present(self):
        """Sanity check that the normal (built) import path is unaffected."""
        assert P.Proxy is not None
        assert P.SerialProxy is not None
        assert issubclass(P.SerialProxy, P.ProxyMixin)
