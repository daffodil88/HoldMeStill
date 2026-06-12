#!/usr/bin/env python3
"""Toggle the SSH server on/off with a push button; reflect state via LED.
Long press powers the device off.

Hardware (BCM pin numbering):
  - Button: GPIO 17, wired to GND (internal pull-up enabled)
  - LED:    GPIO 27, active-high
"""

import os
import signal
import subprocess
import time
from gpiozero import Button, LED

BUTTON_PIN = 17
LED_PIN = 27
SERVICE = "ssh"

# Kill any other running instance so it releases the GPIO pins before we claim them.
_my_pid = os.getpid()
_others = subprocess.run(["pgrep", "-f", "ssh_toggle.py"], capture_output=True, text=True)
for _pid_str in _others.stdout.splitlines():
    _pid = int(_pid_str.strip())
    if _pid != _my_pid:
        os.kill(_pid, signal.SIGTERM)
time.sleep(0.3)


def ssh_is_active() -> bool:
    result = subprocess.run(
        ["systemctl", "is-active", SERVICE],
        capture_output=True, text=True
    )
    return result.stdout.strip() == "active"


def set_ssh(enable: bool) -> None:
    action = "start" if enable else "stop"
    subprocess.run(["systemctl", action, SERVICE], check=True)
    if not enable:
        # systemctl stop kills all sshd processes, but the user's shell on its
        # pts remains alive. Find every pts/N across all who columns (the format
        # includes an extra "sshd" field before the tty on this system) and
        # SIGKILL everything attached to it.
        who = subprocess.run(["who"], capture_output=True, text=True)
        for line in who.stdout.splitlines():
            pts = next((p for p in line.split() if p.startswith("pts/")), None)
            if pts:
                subprocess.run(["pkill", "-KILL", "-t", pts], check=False)


def toggle(_button=None) -> None:
    currently_active = ssh_is_active()
    set_ssh(not currently_active)
    if ssh_is_active():
        led.on()
    else:
        led.off()


_button_held = False


def power_off() -> None:
    global _button_held
    _button_held = True
    for _ in range(5):
        led.on()
        time.sleep(0.1)
        led.off()
        time.sleep(0.1)
    subprocess.run(["poweroff"], check=False)


def on_released() -> None:
    global _button_held
    if _button_held:
        _button_held = False
        return
    toggle()


def shutdown(signum, frame) -> None:
    led.close()
    button.close()
    raise SystemExit(0)


button = Button(BUTTON_PIN, pull_up=True, bounce_time=0.05, hold_time=2.0)
led = LED(LED_PIN)

# Reflect current SSH state on startup
led.on() if ssh_is_active() else led.off()

button.when_held = power_off
button.when_released = on_released

signal.signal(signal.SIGTERM, shutdown)
signal.signal(signal.SIGINT, shutdown)

signal.pause()
