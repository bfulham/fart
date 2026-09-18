"""Headless orchestration: wires the configured plugins to the engine and
runs the tracking/output cycle on a background thread. No UI dependency at
all -- a future Qt UI drives this exact same class, and so can a script or
test, which is the whole point of separating engine/plugins/UI.
"""
from __future__ import annotations

import threading
import time

from .bus import ExternalInputBus, FaderState, TrackerBank
from .engine import CycleState, blank_frames_for_settings, dmx_in_is_needed, run_cycle
from .plugins import CONTROL_INPUT_PLUGINS, OUTPUT_PLUGINS, PSNInPlugin


class Runner:
    def __init__(self, log=None):
        self.log = log or (lambda _msg: None)
        self.trackers = TrackerBank()
        self.bus = ExternalInputBus()
        self.fader = FaderState()
        self.zoom_value = 0.5
        self.iris_value = 1.0
        self.focus_value = 0.5
        self.armed = False
        self.settings = None
        self.running = False
        self.live = None

        self.position_plugin = None
        self.control_plugin = None
        self.output_plugin = None
        self._stop_evt = threading.Event()
        self._thread = None

    def start(self, settings):
        if self.running:
            return
        self.settings = settings
        try:
            self.position_plugin = PSNInPlugin(log=self.log)
            self.position_plugin.start(settings.psn_in, self.trackers)

            if dmx_in_is_needed(settings):
                control_cls = CONTROL_INPUT_PLUGINS[settings.dmx_in.active]
                self.control_plugin = control_cls(log=self.log)
                self.control_plugin.start(getattr(settings.dmx_in, settings.dmx_in.active), self.bus)

            output_cls = OUTPUT_PLUGINS[settings.dmx_out.active]
            self.output_plugin = output_cls()
            self.output_plugin.start(getattr(settings.dmx_out, settings.dmx_out.active))
        except Exception:
            self._stop_plugins()
            raise

        self._stop_evt.clear()
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.log(f"Started: PSN in, DMX in ({settings.dmx_in.active}), DMX out ({settings.dmx_out.active})")

    def _resolve_fader(self):
        if self.settings.fader.source == "dmx_in":
            active = self.settings.dmx_in.active
            universe = getattr(self.settings.dmx_in, active).universe
            value, _age = self.bus.get_channel(universe, self.settings.fader.channel)
            return 0.0 if value is None else value / 255.0
        return self.fader.get()[0]

    def _loop(self):
        state = CycleState()
        try:
            while not self._stop_evt.is_set():
                cycle_start = time.monotonic()
                fader_value = self._resolve_fader()
                frames, statuses = run_cycle(
                    self.settings, self.trackers, self.bus, fader_value,
                    self.zoom_value, self.iris_value, self.focus_value,
                    self.armed, cycle_start, state,
                )
                self.output_plugin.send(frames)
                self.live = {"fader": fader_value, "lights": statuses}
                sleep_time = 1.0 / max(1, self.settings.refresh_hz) - (time.monotonic() - cycle_start)
                time.sleep(max(0.0, sleep_time))
        except Exception as exc:
            self.log("OUTPUT ERROR: " + str(exc))
            self.running = False

    def _stop_plugins(self):
        for plugin in (self.position_plugin, self.control_plugin):
            if plugin:
                try:
                    plugin.stop()
                except Exception:
                    pass
        if self.output_plugin:
            try:
                self.output_plugin.close()
            except Exception:
                pass
        self.position_plugin = self.control_plugin = self.output_plugin = None

    def stop(self):
        self.armed = False
        self._stop_evt.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1)
        self.running = False
        if self.output_plugin and self.settings:
            try:
                self.output_plugin.send(blank_frames_for_settings(self.settings))
                time.sleep(0.05)
            except Exception:
                pass
        self._stop_plugins()
        self.log("Stopped and blacked out all lights")
