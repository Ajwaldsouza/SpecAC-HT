# MicroPython script for XIAO RP2040 boards to control LEDs via PCA9685
import time
import sys
import select
from machine import Pin, I2C
import pca9685

# Initialize I2C and PCA9685
i2c = I2C(1)  # Use appropriate I2C bus
pwm = pca9685.PCA9685(i2c, address=0x40)
pwm.freq(1000)  # Set PWM frequency to 1000 Hz

# LED channel mapping
CHANNELS = {
    'UV': 0,
    'FAR_RED': 1,
    'RED': 2,
    'WHITE': 3,
    'GREEN': 4,
    'BLUE': 5,
}

# Status LED on XIAO RP2040
led_r = Pin(Pin.board.LEDR, Pin.OUT, value=1)
led_g = Pin(Pin.board.LEDG, Pin.OUT, value=1)
led_b = Pin(Pin.board.LEDB, Pin.OUT, value=1)

def set_status_led(r, g, b):
    """Set the RGB LED status (0=on, 1=off due to active-low)"""
    led_r.value(0 if r else 1)
    led_g.value(0 if g else 1)
    led_b.value(0 if b else 1)

def parse_command(cmd):
    """Parse the received command"""
    try:
        parts = cmd.strip().split()
        if parts[0] == "SETALL" and len(parts) == 7:
            # Format: "SETALL d0 d1 d2 d3 d4 d5"
            duty_values = [int(x) for x in parts[1:7]]
            for i, duty in enumerate(duty_values):
                pwm.duty(i, duty)
            return True, "OK"
        else:
            return False, "Invalid command format"
    except Exception as e:
        return False, f"Error: {str(e)}"

def blink_led(n=3):
    """Blink the blue LED to indicate activity"""
    for _ in range(n):
        set_status_led(0, 0, 1)  # Blue on
        time.sleep(0.1)
        set_status_led(0, 0, 0)  # Blue off
        time.sleep(0.1)

def main():
    """Main loop to listen for commands"""
    print("Board controller ready")
    set_status_led(0, 1, 0)  # Green LED for ready state
    
    poller = select.poll()
    poller.register(sys.stdin, select.POLLIN)
    
    while True:
        if poller.poll(0):  # Check if there's data to read (non-blocking)
            cmd = sys.stdin.readline()
            set_status_led(0, 0, 1)  # Blue for processing
            
            success, response = parse_command(cmd)
            
            if success:
                set_status_led(0, 1, 0)  # Green for success
            else:
                set_status_led(1, 0, 0)  # Red for error
                
            print(response)
        time.sleep(0.1)  # Small delay

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        set_status_led(1, 0, 0)  # Red for error
        print(f"Fatal error: {str(e)}")