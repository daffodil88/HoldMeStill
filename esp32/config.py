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
