from machine import I2C, Pin
from pca9685 import PCA9685
import sys

# Initialize I2C and PCA9685
i2c = I2C(0, scl=Pin(21), sda=Pin(20), freq=1000000)  # Adjust pins as needed
pwm = PCA9685(i2c, address=0x40)
pwm.freq(1000)  # Set PWM frequency to 1000 Hz

# Channel mapping
CHANNELS = {'UV': 0, 'FAR_RED': 1, 'RED': 2, 'WHITE': 3, 'GREEN': 4, 'BLUE': 5}

# Function to set all channels
def set_all_duties(duties):
    for i, duty in enumerate(duties):
        pwm.duty(i, min(max(int(duty), 0), 4095))  # Ensure duty is 0-4095

# Main loop to listen for serial commands
while True:
    line = sys.stdin.readline().strip()
    if line.startswith("SETALL"):
        try:
            parts = line.split()
            if len(parts) == 7:  # "SETALL" + 6 duty values
                duties = [int(d) for d in parts[1:]]
                set_all_duties(duties)
        except ValueError:
            pass  # Ignore invalid commands