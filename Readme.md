# Readme

# **SpecAC-HT Control System**

## **Overview**

SpecAC-HT is a comprehensive lighting and environmental control system designed for plant growth chambers. This system allows precise control of LED lighting spectra and environmental conditions through an intuitive GUI interface.

### Features

- **Multi-Channel LED Control**: Independent control of 6 LED channels (UV, Far Red, Red, White, Green, Blue)
- **Multiple Chamber Support**: Control up to 16 individual growth chambers
- **Scheduling Capability**: Set automatic ON/OFF cycles for each chamber
- **Fan Control**: Manage airflow with adjustable fan speed settings
- **Settings Management**: Export and import configuration settings
- **Chamber Mapping**: Persistent mapping between hardware and chamber numbers

## **Hardware Requirements**

- **Microcontroller**: Seeed Studio XIAO RP2040 boards
- **PWM Controller**: PCA9685 16-channel PWM controller modules
- **LEDs**: Multi-channel LED arrays (UV, Far Red, Red, White, Green, Blue)
- **Fans**: DC cooling fans with PWM speed control and tachometer feedback
- **Power Supply**: 5V for microcontroller and suitable power for LED arrays

## **Software Requirements**

- **Microcontroller**: MicroPython (>=1.19)
- **Host Computer**: Python 3.7+ with the following packages:
    - tkinter
    - pyserial
    - json
    - threading

## **Installation**

### Microcontroller Setup

1. Flash MicroPython onto XIAO RP2040 boards
2. Upload the microcontroller code files using `rshell`:
    
    ```r
    main.py
    pca9685.py
    ```
    
3. Connect the PCA9685 to the RP2040's I2C pins (SDA and SCL)
4. Connect LEDs to the corresponding PCA9685 channels
5. Connect fan PWM and tachometer pins

### Host Software Setup

1. Clone the repository
    
    ```r
    git clone https://github.com/Ajwaldsouza/SpecAC-HT.git
    cd SpecAC-HT
    ```
    
2. Install the required Python packages:
    
    ```r
       pip install tkinter pyserial
    ```
    
3. Update the chamber-to-serial mapping (if needed):
    - Edit `microcontroller/microcontroller_serial.txt` to match your chamber numbering
4. Launch the control software:
    
    ```r
    python3 host/led_control_gui.py
    ```
    

## **Usage Guide**

### Initial Setup

1. Start the LED Control GUI application
2. Click "Scan for Boards" to detect all connected chambers
3. The interface will show each detected chamber with individual controls

### Controlling LEDs

1. For each chamber, adjust the intensity (0-100%) for each LED channel
2. Click "Apply" to send the settings to that chamber
3. Use "Apply All Settings" to update all chambers at once

### Scheduling

1. Set ON and OFF times for each chamber (24-hour format)
2. Check "Enable Scheduling" to activate automatic control
3. The system will automatically adjust lighting based on the schedule

### Fan Control

1. Adjust the fan speed percentage in the "Fan Controls" section
2. Click "Apply Fan Settings" to update all chambers
3. Use "Turn Fans ON/OFF" to toggle all fans

### Saving/Loading Settings

1. Use "Export Settings" to save the current configuration
2. Use "Import Settings" to load a previously saved configuration

## **Project Structure**

```r
specac-ht/
├── microcontroller/
│   ├── main.py                # MicroPython code for XIAO RP2040
│   ├── pca9685.py             # PCA9685 PWM driver
│   └── microcontroller_serial.txt  # Chamber to serial number mapping
├── host/
│   └── led_control_gui.py     # GUI application for controlling the system
└── README.md
```

## **Chamber Mapping**

The system maps serial numbers to chamber numbers using the `microcontroller_serial.txt` file with the format:

```r
chamber_number:serial_number
```

For example:

```r
1:4150323038323713000a8f9b56034378

2:4150323038323710003e0d9b56034378
```

## **Troubleshooting**

- **No Boards Detected**: Ensure the XIAO RP2040 boards are connected via USB and have the correct firmware
- **Communication Errors**: Check USB connections and verify the microcontroller is running properly
- **LED Not Responding**: Verify the wiring between the PCA9685 and LED channels
- **Fan Not Responding**: Check PWM and tachometer connections
- **Scheduling Not Working**: Verify time formats are correct (HH:MM, 24-hour format)

---

### Code by Jamie Lawson and Ajwal Dsouza

Developed using Github Co-pilot in Visual Studio Code