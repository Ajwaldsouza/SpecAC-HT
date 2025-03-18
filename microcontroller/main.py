# MicroPython script for XIAO RP2040 boards to control LEDs via PCA9685
import time
import sys
import select
import json
from machine import Pin, I2C, PWM, Timer
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

# Default LED duty values (all off by default)
DEFAULT_DUTY_VALUES = [0, 0, 0, 0, 0, 0]

# Initialize fan control
fan_pwm = PWM(Pin.board.D1, freq=25_000, duty_u16=0)  # starts off
fan_tach = Pin(Pin.board.D0, Pin.IN, Pin.PULL_UP)

# Fan tachometer variables
tach = 0
pps = 0
tach_prev = 0
fan_enabled = False

# Persistent storage filename
LED_STATE_FILE = "led_state.json"
FAN_STATE_FILE = "fan_state.json"

def save_led_state(duty_values):
    """Save LED settings to persistent storage"""
    try:
        with open(LED_STATE_FILE, 'w') as f:
            json.dump(duty_values, f)
        return True
    except:
        return False

def load_led_state():
    """Load LED settings from persistent storage"""
    try:
        with open(LED_STATE_FILE, 'r') as f:
            duty_values = json.load(f)
        
        # Validate the loaded values
        if isinstance(duty_values, list) and len(duty_values) == 6:
            # Make sure all values are integers and in valid range
            valid_values = []
            for val in duty_values:
                if isinstance(val, (int, float)) and 0 <= val <= 4095:
                    valid_values.append(int(val))
                else:
                    valid_values.append(0)
            return valid_values
    except:
        pass
    
    # Return default values if file not found or invalid
    return DEFAULT_DUTY_VALUES

def save_fan_state(enabled, speed):
    """Save fan settings to persistent storage"""
    try:
        fan_state = {"enabled": enabled, "speed": speed}
        with open(FAN_STATE_FILE, 'w') as f:
            json.dump(fan_state, f)
        return True
    except:
        return False

def load_fan_state():
    """Load fan settings from persistent storage"""
    try:
        with open(FAN_STATE_FILE, 'r') as f:
            fan_state = json.load(f)
        
        if isinstance(fan_state, dict) and "enabled" in fan_state and "speed" in fan_state:
            enabled = bool(fan_state["enabled"])
            speed = int(fan_state["speed"])
            if 0 <= speed <= 100:
                return enabled, speed
    except:
        pass
    
    # Return default values if file not found or invalid
    return False, 0

def tach_cb(p):
    """Callback for tachometer readings"""
    global tach
    tach += 1

tach_handler = fan_tach.irq(handler=tach_cb, trigger=Pin.IRQ_FALLING)

def pps_cb(p):
    """Calculate pulses per second for fan speed feedback"""
    global pps, tach_prev
    pps = tach - tach_prev
    tach_prev = tach

# Setup timer for tachometer reading
tim = Timer(freq=1, mode=Timer.PERIODIC, callback=pps_cb)

# Status LED on XIAO RP2040
led_r = Pin(Pin.board.LEDR, Pin.OUT, value=1)
led_g = Pin(Pin.board.LEDG, Pin.OUT, value=1)
led_b = Pin(Pin.board.LEDB, Pin.OUT, value=1)

def set_status_led(r, g, b):
    """Set the RGB LED status (0=on, 1=off due to active-low)"""
    led_r.value(0 if r else 1)
    led_g.value(0 if g else 1)
    led_b.value(0 if b else 1)

def set_fan_speed(percentage):
    """Set the fan speed as a percentage of maximum"""
    global fan_enabled
    if percentage < 0:
        percentage = 0
    elif percentage > 100:
        percentage = 100
    
    # Convert percentage to duty cycle (0-65535)
    duty = int((percentage / 100.0) * 65535)
    fan_pwm.duty_u16(duty)
    fan_enabled = percentage > 0
    
    # Save current fan state to persistent storage
    save_fan_state(fan_enabled, percentage)
    
    return True

def apply_led_settings(duty_values):
    """Apply LED duty values and save them to persistent storage"""
    for i, duty in enumerate(duty_values):
        if i < 6:  # Make sure we don't exceed available channels
            pwm.duty(i, duty)
    
    # Save the settings to persistent storage
    save_led_state(duty_values)

def parse_command(cmd):
    """Parse the received command"""
    try:
        parts = cmd.strip().split()
        if not parts:
            return False, "ERR:EMPTY"
            
        if parts[0] == "SETALL" and len(parts) == 7:
            # Format: "SETALL d0 d1 d2 d3 d4 d5"
            duty_values = [int(x) for x in parts[1:7]]
            # Apply and save settings
            apply_led_settings(duty_values)
            print("OK")  # Make sure to print response
            return True, "OK"
        elif parts[0] == "FAN_SET" and len(parts) == 2:
            # Format: "FAN_SET percentage"
            percentage = int(parts[1])
            set_fan_speed(percentage)
            print("OK")  # Make sure to print response
            return True, "OK"
        elif parts[0] == "FAN_ON":
            # Turn fan on at last speed or default
            if not fan_enabled:
                set_fan_speed(50)  # Default to 50% if turning on
            print("OK")  # Make sure to print response
            return True, "OK"
        elif parts[0] == "FAN_OFF":
            # Turn fan off
            set_fan_speed(0)
            print("OK")  # Make sure to print response
            return True, "OK"
        elif parts[0] == "FAN_STATUS":
            # Return current fan status and speed
            current_duty = fan_pwm.duty_u16()
            speed_pct = int((current_duty / 65535) * 100)
            status_response = f"FAN:{speed_pct}:{pps}"
            print(status_response)  # Make sure to print response
            return True, status_response
        else:
            print("ERR:INVALID_CMD")  # Make sure to print response
            return False, "ERR:INVALID_CMD"
    except Exception as e:
        err_msg = f"ERR:{str(e)}"
        print(err_msg)  # Make sure to print response
        return False, err_msg

def blink_led(n=3):
    """Blink the blue LED to indicate activity"""
    for _ in range(n):
        set_status_led(0, 0, 1)  # Blue on
        time.sleep(0.1)
        set_status_led(0, 0, 0)  # Blue off
        time.sleep(0.1)

def initialize_system():
    """Initialize the system with saved settings"""
    # Indicate system is initializing
    set_status_led(1, 1, 0)  # Yellow = initializing
    
    # Load and apply saved LED settings
    duty_values = load_led_state()
    for i, duty in enumerate(duty_values):
        if i < 6:  # Make sure we don't exceed available channels
            pwm.duty(i, duty)
    
    # Load and apply saved fan settings
    fan_enabled, fan_speed = load_fan_state()
    if fan_enabled:
        set_fan_speed(fan_speed)
    else:
        set_fan_speed(0)
    
    # Blink to indicate initialization complete
    blink_led(2)

def main():
    """Main loop to listen for commands"""
    # Initialize the system with saved settings
    initialize_system()
    
    print("Board controller ready")
    set_status_led(0, 1, 0)  # Green LED for ready state
    
    poller = select.poll()
    poller.register(sys.stdin, select.POLLIN)
    
    while True:
        if poller.poll(0):  # Check if there's data to read (non-blocking)
            cmd = sys.stdin.readline().strip()
            set_status_led(0, 0, 1)  # Blue for processing
            
            # Process command but don't print response here
            # Response is printed in parse_command
            success, _ = parse_command(cmd)
            
            if success:
                set_status_led(0, 1, 0)  # Green for success
            else:
                set_status_led(1, 0, 0)  # Red for error
            
            # No need for print(response) here since we already printed in parse_command
        time.sleep(0.1)  # Small delay

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        set_status_led(1, 0, 0)  # Red for error
        print(f"ERR:FATAL:{str(e)}")