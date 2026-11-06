"""Power supply and charger connection monitor."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)
POWER_SUPPLY_DIR = Path("/sys/class/power_supply")


def get_power_status() -> Tuple[bool, bool, str]:
    """Inspect system power supply to check if AC charger is connected.

    Returns:
        (is_ac_connected, has_battery, description)
    """
    if not POWER_SUPPLY_DIR.exists():
        return True, False, "AC / Wall Power (Desktop)"

    has_battery = False
    ac_connected = False
    battery_percentage: Optional[int] = None

    try:
        for dev in POWER_SUPPLY_DIR.iterdir():
            type_file = dev / "type"
            online_file = dev / "online"
            capacity_file = dev / "capacity"

            dev_type = type_file.read_text(encoding="utf-8").strip() if type_file.exists() else ""

            if dev_type == "Battery":
                has_battery = True
                if capacity_file.exists():
                    try:
                        battery_percentage = int(capacity_file.read_text(encoding="utf-8").strip())
                    except Exception:
                        pass
            elif dev_type in ("Mains", "USB", "USB_PD") or dev.name.startswith(("AC", "ADP")):
                if online_file.exists():
                    try:
                        if online_file.read_text(encoding="utf-8").strip() == "1":
                            ac_connected = True
                    except Exception:
                        pass
    except Exception as e:
        logger.debug("Error reading power supply: %s", e)
        return True, False, f"Power status read error ({e})"

    # If system has no battery, it's a desktop workstation (always AC)
    if not has_battery:
        return True, False, "AC / Desktop Power"

    bat_str = f" ({battery_percentage}%)" if battery_percentage is not None else ""
    if ac_connected:
        return True, True, f"AC Charger Connected{bat_str}"
    else:
        return False, True, f"Battery Power{bat_str} (Discharging)"


def is_ac_connected() -> bool:
    """Returns True if running on AC power / charger, or desktop."""
    ac, _, _ = get_power_status()
    return ac


class PowerMonitor:
    """Background monitor that triggers an event when the charger is plugged in."""

    def __init__(self, on_ac_plugged_callback: Callable[[], None], poll_interval: float = 6.0):
        self.on_ac_plugged_callback = on_ac_plugged_callback
        self.poll_interval = poll_interval
        self._task: Optional[asyncio.Task] = None
        self._last_ac_state: Optional[bool] = None

    def start(self) -> None:
        if self._task is None:
            self._last_ac_state = is_ac_connected()
            self._task = asyncio.create_task(self._monitor_loop())
            logger.info("Power / charger monitor started (current AC state: %s)", self._last_ac_state)

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
            logger.info("Power / charger monitor stopped")

    async def _monitor_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.poll_interval)
                current_ac = is_ac_connected()

                # Transition from Battery (False) -> AC (True)
                if self._last_ac_state is False and current_ac is True:
                    logger.info("AC Charger plugged in! Triggering charger-connected sync.")
                    self.on_ac_plugged_callback()

                self._last_ac_state = current_ac
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("Error in power monitor loop: %s", e)
                await asyncio.sleep(self.poll_interval)
