# SSID of the WiFi network the ESP32 should connect to.
WIFI_SSID = ""

# Password for the WiFi network.
WIFI_PASSWORD = ""

# Whether to mirror lock state to the onboard LED (GPIO 2).
# 1 = LED on while locked, off while unlocked. 0 = LED unused.
LED_ON_LOCK = 1

# Seconds after a random lock during which GET /status hides remaining_seconds.
# The lock is fully active and the timer can be adjusted
# Only the countdown is concealed. 0 = no blind period.
RANDOM_BLIND_SECS = 120

#########################################################################
#*the following is used to configure the optional watchdog circuit. by default it is off and it is strictly optional. to enable it, change WATCHDOG_ENABLED from 0 to 1*#


# Optional hardware watchdog using an Adafruit S-35710 wake-up timer.
# 1 = enabled, 0 = disabled.
WATCHDOG_ENABLED = 0

# ESP32 I2C pins used for the S-35710 breakout.
WATCHDOG_SDA_PIN = 21
WATCHDOG_SCL_PIN = 22

# S-35710 I2C address.
WATCHDOG_I2C_ADDR = 0x32

# How long the external watchdog waits before tripping if not re-armed.
# Must be longer than WATCHDOG_FEED_SECS.
WATCHDOG_TIMEOUT_SECS = 30

# How often the ESP32 re-arms the watchdog while firmware is healthy.
WATCHDOG_FEED_SECS = 5
