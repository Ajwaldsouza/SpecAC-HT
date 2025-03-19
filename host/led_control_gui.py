#!/usr/bin/env python
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import serial
import threading
import time
import json
from serial.tools import list_ports
from datetime import datetime, timedelta

# Constants
MAX_BOARDS = 16
LED_CHANNELS = {
    'UV': 0,
    'FAR_RED': 1,
    'RED': 2,
    'WHITE': 3,
    'GREEN': 4,
    'BLUE': 5
}
LED_COLORS = {
    'UV': "#9400D3",
    'FAR_RED': "#8B0000",
    'RED': "#FF0000",
    'WHITE': "#FFFFFF",
    'GREEN': "#00FF00",
    'BLUE': "#0000FF"
}


class BoardConnection:
    """Manages the connection to a single XIAO RP2040 board"""
    
    def __init__(self, port, serial_number):
        self.port = port
        self.serial_number = serial_number
        self.serial_conn = None
        self.is_connected = False
        self.last_error = ""
        self.fan_speed = 0
        self.fan_enabled = False
        self.max_retries = 3
        self.lock = threading.Lock()  # Add thread lock for thread safety
    def connect(self):
        """Establish serial connection to the board"""
        try:stablish serial connection to the board"""
            self.serial_conn = serial.Serial(
                port=self.port,serial.Serial(
                baudrate=115200,
                timeout=2,  # Increased timeout
                write_timeout=2  # Added write timeout
            )   write_timeout=2
            # Give device time to reset after connection
            time.sleep(2) time to reset after connection
            time.sleep(1)  # Reduced from 2 to 1 second
            # Clear any initialization messages
            if self.serial_conn.in_waiting > 0:
                self.serial_conn.reset_input_buffer()
                self.serial_conn.reset_input_buffer()
            self.is_connected = True
            return Truenected = True
        except serial.SerialException as e:
            self.last_error = str(e)n as e:
            self.is_connected = False
            return Falseected = False
            return False
    def disconnect(self):
        """Close serial connection"""
        if self.serial_conn and self.is_connected:
            try:f.lock:  # Use lock for thread safety
                self.serial_conn.close().is_connected:
            except::
                passself.serial_conn.close()
            finally:pt:
                self.is_connected = False
                finally:
    def send_command(self, duty_values):False
        """Send command to update LED brightness"""
        if not self.is_connected:d_str):
            if not self.connect():response - generic implementation"""
                return False, self.last_errord safety
            if not self.is_connected:
        retry_count = 0self.connect():
        while retry_count < self.max_retries:        
            try:
                # Format: "SETALL d0 d1 d2 d3 d4 d5\n"
                command = "SETALL"lf.max_retries:
                for val in duty_values:
                    command += f" {val}"not present
                command += "\n"and_str.endswith('\n'):
                        command_str += '\n'
                # Clear any pending data
                if self.serial_conn.in_waiting > 0:
                    self.serial_conn.reset_input_buffer()
                        self.serial_conn.reset_input_buffer()
                self.serial_conn.write(command.encode('utf-8'))
                    self.serial_conn.write(command_str.encode('utf-8'))
                # Wait for response with timeout
                start_time = time.time()with timeout
                while time.time() - start_time < 1.0:  # 1 second timeout
                    if self.serial_conn.in_waiting > 0:0:
                        response = self.serial_conn.readline().decode('utf-8').strip()
                        if response == "OK":serial_conn.readline().decode('utf-8').strip()
                            return True, "Success"
                        elif response.startswith("ERR:"):
                            return False, f"Error: {response}"
                        else:   return False, f"Error: {response}"
                            return False, f"Unexpected response: {response}"
                    time.sleep(0.1)urn False, f"Unexpected response: {response}"
                        time.sleep(0.05)  # Smaller sleep for faster response
                retry_count += 1
                if retry_count < self.max_retries:
                    time.sleep(0.5)  # Wait before retry
                else:   time.sleep(0.2)  # Reduced wait between retries
                    return False, "Timeout waiting for response"
                    self.is_connected = False
            except Exception as e:str(e)
                self.is_connected = False
                return False, str(e)es exceeded"
            
        return False, "Max retries exceeded"values):
        """Send command to update LED brightness"""
    def set_fan_speed(self, percentage):
        """Set the fan speed as a percentage"""
        if not self.is_connected:
            if not self.connect():ommand)
                return False, self.last_error
                speed(self, percentage):
        retry_count = 0speed as a percentage"""
        while retry_count < self.max_retries:
            try: message = self.send_command(command)
                command = f"FAN_SET {percentage}\n"
                .fan_speed = percentage
                # Clear any pending datae > 0
                if self.serial_conn.in_waiting > 0:
                    self.serial_conn.reset_input_buffer()
                _on(self):
                self.serial_conn.write(command.encode('utf-8'))
                 message = self.send_command("FAN_ON")
                # Wait for response with timeout
                start_time = time.time()
                while time.time() - start_time < 1.0:  # 1 second timeout
                    if self.serial_conn.in_waiting > 0:
                        response = self.serial_conn.readline().decode('utf-8').strip()
                        if response == "OK":
                            self.fan_speed = percentage
                            self.fan_enabled = percentage > 0
                            return True, "Success"OFF")
                        elif response.startswith("ERR:"):
                            return False, f"Error: {response}"
                        else:0
                            return False, f"Unexpected response: {response}"
                    time.sleep(0.1)
                status(self):
                retry_count += 1s from the board"""
                if retry_count < self.max_retries:
                    time.sleep(0.5)  # Wait before retry
                else:t self.connect():
                    return False, "Timeout waiting for response"
                    
            except Exception as e:
                self.is_connected = False
                return False, str(e)
                # Clear any pending data
        return False, "Max retries exceeded"ng > 0:
                    self.serial_conn.reset_input_buffer()
    def turn_fan_on(self):
        """Turn the fan on"""onn.write(command.encode('utf-8'))
        if not self.is_connected:
            if not self.connect():e
                return False, self.last_error
                while time.time() - start_time < 1.0:
        retry_count = 0self.serial_conn.in_waiting > 0:
        while retry_count < self.max_retries:l_conn.readline().decode('utf-8').strip()
            try:        if response.startswith("FAN:"):
                command = "FAN_ON\n"response.split(":")
                            if len(parts) == 3:
                # Clear any pending data_speed = int(parts[1])
                if self.serial_conn.in_waiting > 0:self.fan_speed > 0
                    self.serial_conn.reset_input_buffer() {self.fan_speed}%, RPM: {parts[2]}"
                        return False, f"Unexpected response: {response}"
                self.serial_conn.write(command.encode('utf-8'))
                    
                # Wait for response with timeoutor response"
                start_time = time.time()
                while time.time() - start_time < 1.0:  # 1 second timeout
                    if self.serial_conn.in_waiting > 0:
                        response = self.serial_conn.readline().decode('utf-8').strip()
                        if response == "OK":
                            self.fan_enabled = True
                            return True, "Success"
                        elif response.startswith("ERR:"):s"""
                            return False, f"Error: {response}"
                        else:
                            return False, f"Unexpected response: {response}"
                    time.sleep(0.1)Control System")
                t.geometry("1200x800")
                retry_count += 1
                if retry_count < self.max_retries:
                    time.sleep(0.5)  # Wait before retry
                else:
                    return False, "Timeout waiting for response"
                    rames = []
            except Exception as e:(board_idx, channel): entry_widget}
                self.is_connected = False
                return False, str(e)
        self.saved_values = {}  # To store values when turning off
        return False, "Max retries exceeded"
            ack master fan state
    def turn_fan_off(self):e
        """Turn the fan off"""k.StringVar(value="50")
        if not self.is_connected:
            if not self.connect():bles - now at board level instead of channel level
                return False, self.last_errordx: {"on_time": time, "off_time": time, "enabled": bool}}
                rd_time_entries = {}   # {board_idx, "on"/"off"): entry_widget}
        retry_count = 0dule_vars = {}  # {board_idx: BooleanVar}
        while retry_count < self.max_retries:
            try:eduler_thread = None
                command = "FAN_OFF\n"
                tion variables
                # Clear any pending data
                if self.serial_conn.in_waiting > 0:
                    self.serial_conn.reset_input_buffer()
                ate_gui()
                self.serial_conn.write(command.encode('utf-8'))
                
                # Wait for response with timeout
                start_time = time.time()
                while time.time() - start_time < 1.0:  # 1 second timeout
                    if self.serial_conn.in_waiting > 0:tk.W, tk.E, tk.S))
                        response = self.serial_conn.readline().decode('utf-8').strip()
                        if response == "OK":
                            self.fan_enabled = False
                            self.fan_speed = 0
                            return True, "Success"
                        elif response.startswith("ERR:"):sticky=(tk.W, tk.E))
                            return False, f"Error: {response}"
                        else:e, text="LED Control System", font=('Helvetica', 16, 'bold')).pack(side=tk.LEFT)
                            return False, f"Unexpected response: {response}"
                    time.sleep(0.1)ton
                ter_button_var = tk.StringVar(value="All Lights OFF")
                retry_count += 1on(
                if retry_count < self.max_retries:
                    time.sleep(0.5)  # Wait before retry
                else:elf.toggle_all_lights,
                    return False, "Timeout waiting for response"
                    
            except Exception as e:.LEFT, padx=20)
                self.is_connected = False
                return False, str(e)on
        self.scheduler_button_var = tk.StringVar(value="Start Scheduler")
        return False, "Max retries exceeded"
            header_frame,
            textvariable=self.scheduler_button_var,
class LEDControlGUI:self.toggle_scheduler,
    """Main GUI application for controlling LED brightness"""
        )
    def __init__(self, root):(side=tk.LEFT, padx=10)
        self.root = root
        self.root.title("SpecAC-HT Control System")
        self.root.geometry("1200x800")rame)
        btn_frame.pack(side=tk.RIGHT)
        self.style = ttk.Style()
        self.style.theme_use('clam')  # Modern theme, command=self.scan_boards).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Apply All Settings", command=self.apply_all_settings).pack(side=tk.LEFT, padx=5)
        self.boards = []
        self.board_frames = [](2 rows x 4 columns grid)
        self.led_entries = {}  # {(board_idx, channel): entry_widget}
        # Track master light state, row=1, sticky=(tk.N, tk.W, tk.E, tk.S))
        self.master_on = Trueigure(0, weight=1)
        self.saved_values = {}  # To store values when turning off
        
        # Track master fan stateard frames
        self.fans_on = Falser = ttk.Frame(boards_frame)
        self.fan_speed_var = tk.StringVar(value="50")tk.BOTH, padx=10, pady=10)
        
        # Scheduling related variables - now at board level instead of channel level
        self.board_schedules = {}  # {board_idx: {"on_time": time, "off_time": time, "enabled": bool}}
        self.board_time_entries = {}   # {board_idx, "on"/"off"): entry_widget}
        self.board_schedule_vars = {}  # {board_idx: BooleanVar}
        self.scheduler_running = False
        self.scheduler_thread = Nonen(nav_frame, text="Previous Page", command=self.prev_page)
        self.prev_button.pack(side=tk.LEFT, padx=10)
        # Pagination variables
        self.current_page = 0.Label(nav_frame, text="Page 1")
        self.boards_per_page = 8e=tk.LEFT, padx=10)
        
        self.create_gui()= ttk.Button(nav_frame, text="Next Page", command=self.next_page)
        self.start_scheduler()side=tk.LEFT, padx=10)
        
    def create_gui(self):me (new)
        """Create the main GUI layout"""frame, text="Fan Controls")
        main_frame = ttk.Frame(self.root, padding="10")ticky=(tk.W, tk.E), pady=10)
        main_frame.grid(column=0, row=0, sticky=(tk.N, tk.W, tk.E, tk.S))
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)value="Turn Fans ON")
        fan_button = ttk.Button(
        # Headerframe,
        header_frame = ttk.Frame(main_frame),
        header_frame.grid(column=0, row=0, columnspan=2, sticky=(tk.W, tk.E))
            width=15
        ttk.Label(header_frame, text="LED Control System", font=('Helvetica', 16, 'bold')).pack(side=tk.LEFT)
        fan_button.grid(column=0, row=0, padx=10, pady=5)
        # Master lights control button
        self.master_button_var = tk.StringVar(value="All Lights OFF")
        master_button = ttk.Button(Fan Speed:").grid(column=1, row=0, padx=(20, 5), pady=5)
            header_frame, ttk.Spinbox(
            textvariable=self.master_button_var,
            command=self.toggle_all_lights,
            width=15
        )   width=5,
        master_button.pack(side=tk.LEFT, padx=20)
            validate='key',
        # Add scheduler control button.register(self.validate_percentage), '%P')
        self.scheduler_button_var = tk.StringVar(value="Start Scheduler")
        scheduler_button = ttk.Button( row=0, padx=5, pady=5)
            header_frame,me, text="%").grid(column=3, row=0, padx=(0, 5), pady=5)
            textvariable=self.scheduler_button_var,
            command=self.toggle_scheduler,
            width=15an_frame, text="Apply Fan Settings", command=self.apply_fan_settings).grid(column=4, row=0, padx=10, pady=5)
        )
        scheduler_button.pack(side=tk.LEFT, padx=10)
        bottom_frame = ttk.Frame(main_frame)
        # Scan and Apply buttons=0, row=3, columnspan=2, sticky=(tk.W, tk.E), pady=5)
        btn_frame = ttk.Frame(header_frame)
        btn_frame.pack(side=tk.RIGHT)="Export Settings", command=self.export_settings).pack(side=tk.LEFT, padx=5)
        ttk.Button(bottom_frame, text="Import Settings", command=self.import_settings).pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_frame, text="Scan for Boards", command=self.scan_boards).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Apply All Settings", command=self.apply_all_settings).pack(side=tk.LEFT, padx=5)
        self.status_var = tk.StringVar(value="Ready")
        # Boards display area (2 rows x 4 columns grid)=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        boards_frame = ttk.Frame(main_frame)umnspan=2, sticky=(tk.W, tk.E))
        boards_frame.grid(column=0, row=1, sticky=(tk.N, tk.W, tk.E, tk.S))
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(1, weight=1)
        next_page(self):
        # Container frame for board framesards"""
        self.boards_container = ttk.Frame(boards_frame)_page - 1) // self.boards_per_page
        self.boards_container.pack(expand=True, fill=tk.BOTH, padx=10, pady=10)
            self.current_page += 1
        # Navigation frame for page buttons
        nav_frame = ttk.Frame(boards_frame)
        nav_frame.pack(fill=tk.X, pady=5)
        """Navigate to the previous page of boards"""
        # Page navigation buttons
        self.prev_button = ttk.Button(nav_frame, text="Previous Page", command=self.prev_page)
        self.prev_button.pack(side=tk.LEFT, padx=10)
        
        self.page_label = ttk.Label(nav_frame, text="Page 1")
        self.page_label.pack(side=tk.LEFT, padx=10)age of boards"""
        # Update page label
        self.next_button = ttk.Button(nav_frame, text="Next Page", command=self.next_page)r_page)
        self.next_button.pack(side=tk.LEFT, padx=10)rent_page + 1} of {max_pages}")
        
        # Fan control frame (new)on buttons as needed
        fan_frame = ttk.LabelFrame(main_frame, text="Fan Controls")e > 0 else tk.DISABLED)
        fan_frame.grid(column=0, row=2, columnspan=2, sticky=(tk.W, tk.E), pady=10)1 else tk.DISABLED)
        
        # Fan toggle buttonames first
        self.fan_button_var = tk.StringVar(value="Turn Fans ON")
        fan_button = ttk.Button(
            fan_frame,
            textvariable=self.fan_button_var,t page
            command=self.toggle_all_fans,elf.boards_per_page
            width=15ge(start_idx, min(start_idx + self.boards_per_page, len(self.board_frames))):
        )   # Calculate the row and column (2 rows x 4 columns grid)
        fan_button.grid(column=0, row=0, padx=10, pady=5)
            col = (i - start_idx) % 4
        # Fan speed controles[i].grid(row=row, column=col, padx=5, pady=5)
        ttk.Label(fan_frame, text="Fan Speed:").grid(column=1, row=0, padx=(20, 5), pady=5)
        fan_speed_entry = ttk.Spinbox(
            fan_frame,es for each detected board"""
            from_=0, frames
            to=100,n self.board_frames:
            width=5,stroy()
            textvariable=self.fan_speed_var,
            validate='key',
            validatecommand=(self.root.register(self.validate_percentage), '%P')
        )   frame = ttk.LabelFrame(self.boards_container, text=f"Board {i+1}: {board.serial_number}")
        fan_speed_entry.grid(column=2, row=0, padx=5, pady=5)
        ttk.Label(fan_frame, text="%").grid(column=3, row=0, padx=(0, 5), pady=5)
            # LED control section
        # Apply fan settings button.LabelFrame(frame, text="LED Controls")
        ttk.Button(fan_frame, text="Apply Fan Settings", command=self.apply_fan_settings).grid(column=4, row=0, padx=10, pady=5)
            
        # Bottom frame for Import/Export buttons
        bottom_frame = ttk.Frame(main_frame)t="LED Channel").grid(column=1, row=0, sticky=tk.W, padx=5)
        bottom_frame.grid(column=0, row=3, columnspan=2, sticky=(tk.W, tk.E), pady=5)sticky=tk.W, padx=5)
            
        ttk.Button(bottom_frame, text="Export Settings", command=self.export_settings).pack(side=tk.LEFT, padx=5)
        ttk.Button(bottom_frame, text="Import Settings", command=self.import_settings).pack(side=tk.RIGHT, padx=5)
                color_frame = ttk.Frame(led_control_frame, width=20, height=20)
        # Status barr_frame.grid(column=0, row=row, padx=5, pady=2)
        self.status_var = tk.StringVar(value="Ready")g=LED_COLORS[channel_name], width=2)
        status_bar = ttk.Label(main_frame, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.grid(column=0, row=4, columnspan=2, sticky=(tk.W, tk.E))
                ttk.Label(led_control_frame, text=channel_name).grid(column=1, row=row, sticky=tk.W, padx=5)
        self.scan_boards()
                value_var = tk.StringVar(value="0")
    def next_page(self):ttk.Spinbox(
        """Navigate to the next page of boards"""
        max_pages = (len(self.boards) + self.boards_per_page - 1) // self.boards_per_page
        if self.current_page < max_pages - 1:
            self.current_page += 1
            self.update_page_display()_var,
                    validate='key',
    def prev_page(self):datecommand=(self.root.register(self.validate_percentage), '%P')
        """Navigate to the previous page of boards"""
        if self.current_page > 0:=2, row=row, sticky=tk.W, padx=5)
            self.current_page -= 1rol_frame, text="%").grid(column=3, row=row, sticky=tk.W)
            self.update_page_display()
                self.led_entries[(i, channel_name)] = entry
    def update_page_display(self):
        """Update the display to show the current page of boards"""
        # Update page label= ttk.LabelFrame(frame, text="Board Schedule")
        max_pages = max(1, (len(self.boards) + self.boards_per_page - 1) // self.boards_per_page)
        self.page_label.config(text=f"Page {self.current_page + 1} of {max_pages}")
            # Create schedule controls
        # Enable/disable navigation buttons as needed).grid(column=0, row=0, padx=5, pady=5, sticky=tk.W)
        self.prev_button.config(state=tk.NORMAL if self.current_page > 0 else tk.DISABLED)
        self.next_button.config(state=tk.NORMAL if self.current_page < max_pages - 1 else tk.DISABLED)
            on_time.grid(column=1, row=0, padx=5, pady=5)
        # Hide all board frames firsti, "on")] = on_time
        for frame in self.board_frames:
            frame.grid_remove()frame, text="OFF Time:").grid(column=2, row=0, padx=5, pady=5, sticky=tk.W)
            off_time_var = tk.StringVar(value="20:00")
        # Show only the boards for the current pagedth=7, textvariable=off_time_var)
        start_idx = self.current_page * self.boards_per_page
        for i in range(start_idx, min(start_idx + self.boards_per_page, len(self.board_frames))):
            # Calculate the row and column (2 rows x 4 columns grid)
            row = (i - start_idx) // 4
            col = (i - start_idx) % 4Var(value=False)
            self.board_frames[i].grid(row=row, column=col, padx=5, pady=5)
                schedule_frame, 
    def create_board_frames(self):uling",
        """Create frames for each detected board"""optimized version"""
        # Remove old framesbda b_idx=i: self.update_board_schedule(b_idx)
        for frame in self.board_frames:
            frame.destroy()grid(column=4, row=0, padx=10, pady=5, sticky=tk.W)
        self.board_frames = []e_vars[i] = schedule_var
        self.led_entries = {}  # Clear old entries    
        for i, board in enumerate(self.boards):
            frame = ttk.LabelFrame(self.boards_container, text=f"Board {i+1}: {board.serial_number}")
            self.board_frames.append(frame)elf.validate_percentage)
            "off_time": "20:00",
            # LED control section at once
            led_control_frame = ttk.LabelFrame(frame, text="LED Controls")
            led_control_frame.grid(column=0, row=0, padx=5, pady=5, sticky=(tk.W, tk.E))ial_number}")
            self.board_frames.append(frame)    
            # Add header row for LED controls
            ttk.Label(led_control_frame, text="LED Channel").grid(column=1, row=0, sticky=tk.W, padx=5)
            ttk.Label(led_control_frame, text="Intensity (%)").grid(column=2, row=0, sticky=tk.W, padx=5)
            led_control_frame.grid(column=0, row=0, padx=5, pady=5, sticky=(tk.W, tk.E))    text="Apply", 
            # Add LED controls for each channel
            for row, (channel_name, channel_idx) in enumerate(LED_CHANNELS.items(), start=1):
                color_frame = ttk.Frame(led_control_frame, width=20, height=20)=0, sticky=tk.W, padx=5)
                color_frame.grid(column=0, row=row, padx=5, pady=2)(column=2, row=0, sticky=tk.W, padx=5)
                color_label = tk.Label(color_frame, bg=LED_COLORS[channel_name], width=2)
                color_label.pack(fill=tk.BOTH, expand=True)
                row, (channel_name, channel_idx) in enumerate(LED_CHANNELS.items(), start=1):
                ttk.Label(led_control_frame, text=channel_name).grid(column=1, row=row, sticky=tk.W, padx=5)
                color_frame.grid(column=0, row=row, padx=5, pady=2)e all lights on or off on all boards"""
                value_var = tk.StringVar(value="0") bg=LED_COLORS[channel_name], width=2)
                entry = ttk.Spinbox(l=tk.BOTH, expand=True)No Boards", "No boards available to control.")
                    led_control_frame, 
                    from_=0, _control_frame, text=channel_name).grid(column=1, row=row, sticky=tk.W, padx=5)
                    to=100, 
                    width=5, k.StringVar(value="0")ghts - keep UI values but send zeros to boards
                    textvariable=value_var,
                    validate='key',me, set("All Lights ON")
                    validatecommand=(self.root.register(self.validate_percentage), '%P')
                )   to=100, e current values but don't change the UI
                entry.grid(column=2, row=row, sticky=tk.W, padx=5)
                ttk.Label(led_control_frame, text="%").grid(column=3, row=row, sticky=tk.W)
                    validate='key',for channel_name in LED_CHANNELS:
                self.led_entries[(i, channel_name)] = entry
                )        if key in self.led_entries:
            # Scheduling section - one per boardicky=tk.W, padx=5)
            schedule_frame = ttk.LabelFrame(frame, text="Board Schedule")=row, sticky=tk.W)].get()
            schedule_frame.grid(column=0, row=1, padx=5, pady=5, sticky=(tk.W, tk.E))
                self.led_entries[(i, channel_name)] = entry                pass
            # Create schedule controls
            ttk.Label(schedule_frame, text="ON Time:").grid(column=0, row=0, padx=5, pady=5, sticky=tk.W)
            on_time_var = tk.StringVar(value="08:00")xt="Board Schedule")
            schedule_frame.grid(column=0, row=1, padx=5, pady=5, sticky=(tk.W, tk.E))
            
            # Create schedule controls
            ttk.Label(schedule_frame, text="ON Time:").grid(column=0, row=0, padx=5, pady=5, sticky=tk.W)        
            on_time_var = tk.StringVar(value="08:00")ings preserved)")
            on_time = ttk.Entry(schedule_frame, width=7, textvariable=on_time_var)
            on_time.grid(column=1, row=0, padx=5, pady=5)
            self.board_time_entries[(i, "on")] = on_timey in the UI
            
            ttk.Label(schedule_frame, text="OFF Time:").grid(column=2, row=0, padx=5, pady=5, sticky=tk.W)self.master_button_var.set("All Lights OFF")
            off_time_var = tk.StringVar(value="20:00")
            off_time = ttk.Entry(schedule_frame, width=7, textvariable=off_time_var)e UI
            off_time.grid(column=3, row=0, padx=5, pady=5)
            self.board_time_entries[(i, "off")] = off_time"All lights restored to displayed settings")
            
            # Schedule enable checkbox
            schedule_var = tk.BooleanVar(value=False)
            schedule_check = ttk.Checkbutton(lf.scheduler_running:
                schedule_frame, 
                text="Enable Scheduling",uler")
                variable=schedule_var,self.status_var.set("Scheduler stopped")
                command=lambda b_idx=i: self.update_board_schedule(b_idx)
            )ue
            schedule_check.grid(column=4, row=0, padx=10, pady=5, sticky=tk.W)ar.set("Stop Scheduler")
            self.board_schedule_vars[i] = schedule_vareduler started")
            _thread or not self.scheduler_thread.is_alive():
            # Initialize the schedule data for this boarder()
            self.board_schedules[i] = {
                "on_time": "08:00",heduler(self):
                "off_time": "20:00","""
                "enabled": False,running = True
                "saved_values": {}button_var.set("Stop Scheduler")
            }= threading.Thread(target=self.schedule_checker, daemon=True)
                
            # Individual apply button
            ttk.Button(is_time_between(self, current_time, start_time, end_time):
                frame, nd end times, handling overnight periods"""
                text="Apply", o datetime for comparison
                command=lambda b_idx=i: self.apply_board_settings(b_idx)e(current_time, "%H:%M")
            ).grid(column=0, row=2, pady=10, sticky=(tk.W, tk.E))    start = datetime.strptime(start_time, "%H:%M")
        end_time, "%H:%M")
        # Update the display to show the first pageoptimized"""
        self.current_page = 0
            messagebox.showwarning("No Boards", "No boards available to control.")
            returnent <= end
        toggle_all_lights(self):else:
        if self.master_on:ts on or off on all boards"""case: end time is before start time (e.g., 20:00 to 08:00 next day)
            # Turn OFF all lights - keep UI values but send zeros to boards
            self.master_on = False("No Boards", "No boards available to control.")
            self.master_button_var.set("All Lights ON")
             settings for a specific board"""
            # Save current values but don't change the UI
            self.saved_values = {}- keep UI values but send zeros to boardsError", "Invalid board index")
            for board_idx in range(len(self.boards)):
                for channel_name in LED_CHANNELS:s ON")
                    key = (board_idx, channel_name)
                    if key in self.led_entries:nge the UI
                        try:s = {}
                            self.saved_values[key] = self.led_entries[key].get()
                        except (ValueError, KeyError):
                            pass_idx, channel_name)rd_schedules:
                    if key in self.led_entries:scheduling_enabled = self.board_schedules[board_idx].get("enabled", False)
            # Use threads to apply settings to all boards in parallel
            self.status_var.set("Turning off all lights...")elf.saved_values[key] = self.led_entries[key].get():
            self.root.update_idletasks()  # Update UI immediately:
            
            threads = []me", "08:00")
            for board_idx in range(len(self.boards)):eros to all boards without changing UI = self.board_schedules[board_idx].get("off_time", "20:00")
                thread = threading.Thread(
                    target=self.send_zeros_to_board,for board_idx in range(len(self.boards)):# Check if current time is within the ON period
                    args=(board_idx,)   if self.send_zeros_to_board(board_idx):f not self.is_time_between(current_time, on_time, off_time):
                )
                threads.append(thread)
                thread.start()on {success_count}/{len(self.boards)} boards (settings preserved)")
                # Save the current UI values for later use but don't change UI
            # Wait for all threads to complete (with timeout)
            for thread in threads:ply the values already in the UI
                thread.join(timeout=2.0)
                        self.master_button_var.set("All Lights OFF")                except (ValueError, KeyError):
            self.status_var.set(f"All lights turned OFF (settings preserved)")"
            dy in the UI
        else:s()es
            # Turn ON all lights - apply the values already in the UIs restored to displayed settings")_idx]["saved_values"] = saved_values
            self.master_on = True
            self.master_button_var.set("All Lights OFF")ng UI
            able or disable the scheduler"""   success = self.send_zeros_to_board(board_idx)
            # Apply the values that are already in the UI
            self.status_var.set("Restoring all lights...")
            self.root.update_idletasks()  # Update UI immediatelyeduler")rd_idx+1}: Settings saved, lights off (outside scheduled hours)")
            self.apply_all_settings()
            self.status_var.set("All lights restored to displayed settings")Error turning lights off for schedule")
            self.scheduler_running = True                
    def toggle_scheduler(self):ton_var.set("Stop Scheduler")
        """Enable or disable the scheduler"""er started")
        if self.scheduler_running:ead or not self.scheduler_thread.is_alive():ach channel
            self.scheduler_running = False
            self.scheduler_button_var.set("Start Scheduler")
            self.status_var.set("Scheduler stopped")tries[(board_idx, channel)].get())
        else:    """Start the scheduler thread"""            duty = int((percentage / 100.0) * 4095)
            self.scheduler_running = True
            self.scheduler_button_var.set("Stop Scheduler")
            self.status_var.set("Scheduler started")t=self.schedule_checker, daemon=True)
            if not self.scheduler_thread or not self.scheduler_thread.is_alive():
                self.start_scheduler()
    time, end_time):
    def start_scheduler(self):"""Check if current time is between start and end times, handling overnight periods"""    saved_values = {}
        """Start the scheduler thread"""mes to datetime for comparisonin LED_CHANNELS:
        self.scheduler_running = True
        self.scheduler_button_var.set("Stop Scheduler")me, "%H:%M")= self.led_entries[(board_idx, channel)].get()
        self.scheduler_thread = threading.Thread(target=self.schedule_checker, daemon=True) datetime.strptime(end_time, "%H:%M")   except:
        self.scheduler_thread.start()
    
    def is_time_between(self, current_time, start_time, end_time):        # Simple case: start time is before end time (e.g., 08:00 to 20:00)    
        """Check if current time is between start and end times, handling overnight periods"""
        # Convert all times to datetime for comparison
        current = datetime.strptime(current_time, "%H:%M")is before start time (e.g., 20:00 to 08:00 next day)
        start = datetime.strptime(start_time, "%H:%M")
        end = datetime.strptime(end_time, "%H:%M")d:
        y_board_settings(self, board_idx):    self.status_var.set(f"Board {board_idx+1}: Settings applied (within scheduled hours)")
        if start <= end:ic board"""
            # Simple case: start time is before end time (e.g., 08:00 to 20:00)len(self.boards):tus_var.set(f"Board {board_idx+1}: Settings applied successfully")
            return start <= current <= end    messagebox.showerror("Error", "Invalid board index")else:# Check if scheduling is enabled for this board
        else:
            # Wrap-around case: end time is before start time (e.g., 20:00 to 08:00 next day)rror - {message}")_schedules:
            return current >= start or current <= end
    
    def apply_board_settings(self, board_idx):"""Update the schedule for a specific board"""current_time = datetime.now().strftime("%H:%M")
        """Apply settings for a specific board"""is enabled for this boardlf.board_schedules:
        if board_idx >= len(self.boards):oard_idx] = {"enabled": False}
            messagebox.showerror("Error", "Invalid board index")
            return", False)
            
        board = self.boards[board_idx]cheduling_enabled:eck if current time is within the ON period
        duty_values = []
        _idx, "on")].get()EDs
        # Check if scheduling is enabled for this board)
        scheduling_enabled = False_schedules[board_idx].get("off_time", "20:00")self.board_time_entries:
        if board_idx in self.board_schedules:rd_schedules[board_idx]["off_time"] = self.board_time_entries[(board_idx, "off")].get()# Save the current UI values for later use but don't change UI
            scheduling_enabled = self.board_schedules[board_idx].get("enabled", False)
        ent_time, on_time, off_time):
        if scheduling_enabled:e of ON period - save the settings but turn off LEDs}saved_values[channel_name] = self.led_entries[(board_idx, channel_name)].get()
            # Get the current time
            current_time = datetime.now().strftime("%H:%M")
            on_time = self.board_schedules[board_idx].get("on_time", "08:00")ter use but don't change UI
            off_time = self.board_schedules[board_idx].get("off_time", "20:00")for channel in LED_CHANNELS:try:# Store saved values
             self.led_entries[key].get()[board_idx]["saved_values"] = saved_values
            # Check if current time is within the ON periodhannel)].get()
            if not self.is_time_between(current_time, on_time, off_time):    except (ValueError, KeyError):    saved_values[channel_name] = "0"# Send all zeros to the board (lights off) without changing UI
                # Outside of ON period - save the settings but turn off LEDs
                saved_values = {}
                # Store saved valuesss:
                # Save the current UI values for later use but don't change UIschedules[board_idx]["saved_values"] = saved_values just enabled, check if we need to turn off lightsatus_var.set(f"Board {board_idx+1}: Settings saved, lights off (outside scheduled hours)")
                for channel in LED_CHANNELS:
                    try:d all zeros to the board (lights off) without changing UIime = datetime.now().strftime("%H:%M")elf.status_var.set(f"Board {board_idx+1}: Error turning lights off for schedule")
                        saved_values[channel] = self.led_entries[(board_idx, channel)].get()
                    except (ValueError, KeyError):elf.board_schedules[board_idx].get("off_time", "20:00")rn
                        saved_values[channel] = "0"cess:
                            self.status_var.set(f"Board {board_idx+1}: Settings saved, lights off (outside scheduled hours)")    if not self.is_time_between(current_time, on_time, off_time):# Get duty cycle values for each channel
                # Store saved values
                self.board_schedules[board_idx]["saved_values"] = saved_valuesset(f"Board {board_idx+1}: Error turning lights off for schedule")f"Board {board_idx+1}: Schedule enabled, outside ON hours - lights off but settings preserved")NELS.keys():
                    
                # Send all zeros to the board (lights off) without changing UI
                success = self.send_zeros_to_board(board_idx)
                 channel
                if success:NNELS:lf, board_idx):rcentage / 100.0) * 4095)
                    self.status_var.set(f"Board {board_idx+1}: Settings saved, lights off (outside scheduled hours)")ues"""ty)
                else:        percentage = int(self.led_entries[(board_idx, channel)].get())if board_idx >= len(self.boards):    except (ValueError, KeyError):
                    self.status_var.set(f"Board {board_idx+1}: Error turning lights off for schedule")
                    pend(duty)
                return:oard_idx]abled, save these values for future use
        tly
        # Get duty cycle values for each channel.send_command_with_values([0, 0, 0, 0, 0, 0])lues = {}
        for channel in LED_CHANNELS:
            try:nabled:
                percentage = int(self.led_entries[(board_idx, channel)].get())
                duty = int((percentage / 100.0) * 4095)
                duty_values.append(duty)        try:"""Background thread to check and apply scheduled settings"""            saved_values[channel] = "0"
            except ValueError:annel] = self.led_entries[(board_idx, channel)].get()values"] = saved_values
                duty_values.append(0)
                    saved_values[channel] = "0"        time.sleep(1)# Send command to the board using the optimized method
        # If scheduling is enabled, save these values for future useard_schedules[board_idx]["saved_values"] = saved_valuestinuessage = board.send_command_with_values(duty_values)
        if scheduling_enabled:
            saved_values = {}
            for channel in LED_CHANNELS:message = board.send_command(duty_values)es_made = Falseheduling_enabled:
                try:
                    saved_values[channel] = self.led_entries[(board_idx, channel)].get()ccess:or board_idx, schedule_info in self.board_schedules.items():lse:
                except:
                    saved_values[channel] = "0"d (within scheduled hours)")
            self.board_schedules[board_idx]["saved_values"] = saved_values        else:                        messagebox.showerror(f"Error - Board {board_idx+1}", message)
        ard_idx+1}: Settings applied successfully")_time", "")idx+1}: Error - {message}")
        # Send command to the board
        success, message = board.send_command(duty_values){board_idx+1}", message)
        message}")
        if success:# Time to turn on all LEDs on this boardoard_idx not in self.board_schedules:
            if scheduling_enabled:_idx):schedule_info and schedule_info["saved_values"]:x] = {"enabled": False}
                self.status_var.set(f"Board {board_idx+1}: Settings applied (within scheduled hours)")
            else:if board_idx not in self.board_schedules:                    key = (board_idx, channel_name)# Get current values from widgets
                self.status_var.set(f"Board {board_idx+1}: Settings applied successfully"): False}.board_schedule_vars[board_idx].get()
        else:
            messagebox.showerror(f"Error - Board {board_idx+1}", message)t current values from widgets                    self.led_entries[key].insert(0, value)board_idx, "on") in self.board_time_entries:
            self.status_var.set(f"Board {board_idx+1}: Error - {message}").board_schedule_vars[board_idx].get()e_entries[(board_idx, "on")].get()
    
    def update_board_schedule(self, board_idx):board_idx, "on") in self.board_time_entries:        board_idx, "off") in self.board_time_entries:
        """Update the schedule for a specific board"""oard_idx]["on_time"] = self.board_time_entries[(board_idx, "on")].get()urrent_time:oard_idx]["off_time"] = self.board_time_entries[(board_idx, "off")].get()
        if board_idx not in self.board_schedules: all LEDs on this board
            self.board_schedules[board_idx] = {"enabled": False}rd_time_entries: for all channels before turning off
            ["off_time"] = self.board_time_entries[(board_idx, "off")].get()
        # Get current values from widgets
        self.board_schedules[board_idx]["enabled"] = self.board_schedule_vars[board_idx].get()ent LED values    key = (board_idx, channel_name)oard_idx, channel_name)
        
        if (board_idx, "on") in self.board_time_entries:e in LED_CHANNELS:     saved_values[channel_name] = self.led_entries[key].get()
            self.board_schedules[board_idx]["on_time"] = self.board_time_entries[(board_idx, "on")].get()k.END)f.led_entries[key].get()
                if key in self.led_entries:                    self.led_entries[key].insert(0, "0")        except:
        if (board_idx, "off") in self.board_time_entries:
            self.board_schedules[board_idx]["off_time"] = self.board_time_entries[(board_idx, "off")].get()            saved_values[channel_name] = self.led_entries[key].get()            schedule_info["saved_values"] = saved_values
            
        # Store current LED valuesdx+1}: Schedule activated - turning OFF")
        saved_values = {}
        for channel_name in LED_CHANNELS:
            key = (board_idx, channel_name)
            if key in self.led_entries: scheduling was just enabled, check if we need to turn off lights    self.root.after(0, self.apply_all_settings)on_time = self.board_schedules[board_idx].get("on_time", "08:00")
                try:
                    saved_values[channel_name] = self.led_entries[key].get()
                except:
                    saved_values[channel_name] = "0"time = self.board_schedules[board_idx].get("off_time", "20:00")rds(self):# Outside of ON period - turn off lights but keep UI values
        
        self.board_schedules[board_idx]["saved_values"] = saved_valuese, on_time, off_time):
            for board in self.boards:            # Outside of ON period - turn off lights but keep UI values            # Send direct command to turn off LEDs without changing UI
        # If scheduling was just enabled, check if we need to turn off lightschedule enabled, outside ON hours - lights off but settings preserved")d_idx)
        if self.board_schedules[board_idx]["enabled"]:
            current_time = datetime.now().strftime("%H:%M")dx):
            on_time = self.board_schedules[board_idx].get("on_time", "08:00")f.board_frames:d_zeros_to_board(board_idx) the board without changing UI values"""
            off_time = self.board_schedules[board_idx].get("off_time", "20:00")frame.destroy() >= len(self.boards):
            
            if not self.is_time_between(current_time, on_time, off_time):"
                # Outside of ON period - turn off lights but keep UI values
                self.status_var.set(f"Board {board_idx+1}: Schedule enabled, outside ON hours - lights off but settings preserved")# Reset master button state    return False# Send command with all zeros directly
                 = True.send_command([0, 0, 0, 0, 0, 0])
                # Send direct command to turn off LEDs without changing UI        self.master_button_var.set("All Lights OFF")        board = self.boards[board_idx]        
                self.send_zeros_to_board(board_idx)zeros directly
    
    def send_zeros_to_board(self, board_idx):nnected boards:minute = -1  # Track the last minute we checked
        """Send zeros to the board without changing UI values"""
        if board_idx >= len(self.boards): self.detect_xiao_boards()
            return Falseuler_running:scheduler_running:
            ot detected_boards:round thread to check and apply scheduled settings"""time.sleep(1)time.sleep(1)
        board = self.boards[board_idx]No XIAO RP2040 boards were detected.")
        # Send command with all zeros directlyset("No boards found")r_running:
        success, message = board.send_command([0, 0, 0, 0, 0, 0])    returncurrent_time = datetime.now().strftime("%H:%M")    time.sleep(1)# Get current time - only check once per minute for efficiency
        
        return success
_number in detected_boards:chedule_info in self.board_schedules.items():atetime.now().strftime("%H:%M")
    def schedule_checker(self):.boards.append(BoardConnection(port, serial_number))ot schedule_info.get("enabled", False):made = Falserocess if the minute has changed
        """Background thread to check and apply scheduled settings"""
        while True:
            if not self.scheduler_running:.create_board_frames()on_time = schedule_info.get("on_time", "")if not schedule_info.get("enabled", False):current_time = now.strftime("%H:%M")
                time.sleep(1)
                continueoard(s)")
                
            current_time = datetime.now().strftime("%H:%M")
            changes_made = False_info["saved_values"]:
            
            for board_idx, schedule_info in self.board_schedules.items():
                if not schedule_info.get("enabled", False):
                    continue.END)alue in schedule_info["saved_values"].items():o.get("off_time", "")
                    
                on_time = schedule_info.get("on_time", "")append([port_info.device, port_info.serial_number])    changes_made = True        if key in self.led_entries:if on_time == current_time or off_time == current_time:
                off_time = schedule_info.get("off_time", "")edule activated - turning ON")es[key].delete(0, tk.END) need updating rather than updating immediately
                
                if on_time == current_time:
                    # Time to turn on all LEDs on this boardalid percentage (0-100)"""f all LEDs on this boardar.set(f"Board {board_idx+1}: Schedule activated - turning ON")
                    if "saved_values" in schedule_info and schedule_info["saved_values"]:
                        for channel_name, value in schedule_info["saved_values"].items():
                            key = (board_idx, channel_name)n thread
                            if key in self.led_entries:
                                self.led_entries[key].delete(0, tk.END)
                                self.led_entries[key].insert(0, value)
                        changes_made = Truealse        self.led_entries[key].delete(0, tk.END)    key = (board_idx, channel_name)ep(1)
                        self.status_var.set(f"Board {board_idx+1}: Schedule activated - turning ON")
                    d_entries[key].get() current_time, boards_to_update):
                elif off_time == current_time:
                    # Time to turn off all LEDs on this boardess_count = 0        changes_made = True                self.led_entries[key].insert(0, "0")board_idx in boards_to_update:
                    # Save current values for all channels before turning offhedule activated - turning OFF")
                    saved_values = {}le_info.get("on_time", "")
                    for channel_name in LED_CHANNELS:
                        key = (board_idx, channel_name)hanges_made:    self.status_var.set(f"Board {board_idx+1}: Schedule activated - turning OFF")
                        if key in self.led_entries:
                            saved_values[channel_name] = self.led_entries[key].get()            success_count += 1                    # Apply changes if any were made            # Time to turn on all LEDs on this board
                            self.led_entries[key].delete(0, tk.END)on as e:  # Check every 10 secondse:values" in schedule_info and schedule_info["saved_values"]:
                            self.led_entries[key].insert(0, "0")
                    
                    schedule_info["saved_values"] = saved_valueslied settings to {success_count} board(s), {error_count} error(s)")connections to XIAO RP2040 boards"""ck every 10 seconds self.led_entries:
                    changes_made = TrueND)
                    self.status_var.set(f"Board {board_idx+1}: Schedule activated - turning OFF")self):f.boards:):    self.led_entries[key].insert(0, value)
            """Toggle all fans on or off on all boards"""    board.disconnect()"""Detect and initialize connections to XIAO RP2040 boards"""            
            # Apply changes if any were madex+1}: Schedule activated - turning ON")
            if changes_made:warning("No Boards", "No boards available to control fans.")this specific board
                self.root.after(0, self.apply_all_settings)ard_settings(board_idx)
                
            time.sleep(10)  # Check every 10 secondsif self.fans_on:self.board_frames = []    elif off_time == current_time:
    Ds on this board
    def scan_boards(self):lse
        """Detect and initialize connections to XIAO RP2040 boards"""
        # Clear previous boards and GUI elementsater use but don't change UI
        for board in self.boards:    success_count = 0self.master_button_var.set("All Lights OFF")        for channel_name in LED_CHANNELS:
            board.disconnect()rate(self.boards):dx, channel_name)
        self.boards = []    success, message = board.turn_fan_off()ter_on = True        if key in self.led_entries:
        
        for frame in self.board_frames:        success_count += 1.saved_values = {}    
            frame.destroy()ards()
        self.board_frames = []
        self.led_entries = {}
        s_var.set(f"All fans turned OFF on {success_count}/{len(self.boards)} boards")ebox.showwarning("No Boards Found", "No XIAO RP2040 boards were detected.")oards = self.detect_xiao_boards()tatus_var.set(f"Board {board_idx+1}: Schedule activated - turning OFF")
        # Reset master button state.status_var.set("No boards found").send_zeros_to_board(board_idx)
        else:
            # Turn on all fans
            self.fans_on = True
            self.fan_button_var.set("Turn Fans OFF")port, serial_number in detected_boards:    returnear previous boards and GUI elements
            , serial_number))
            # Get the speed from the entry
            try:detected_boards = self.detect_xiao_boards()# Create GUI elements for boardsfor port, serial_number in detected_boards:.boards = []
                speed = int(self.fan_speed_var.get())
            except ValueError:ards:
                speed = 50  # Default to 50% if invalidO RP2040 boards were detected.")s)")
                self.fan_speed_var.set("50")d")
                        messagebox.showerror("Error Scanning Boards", str(e))            return            self.led_entries = {}
            success_count = 0"Error: {str(e)}")boards)} board(s)")
            for i, board in enumerate(self.boards):
                success, message = board.set_fan_speed(speed)boards(self):, serial_number in detected_boards:ox.showerror("Error Scanning Boards", str(e))on = True
                if success:
                    success_count += 1
                else:in list_ports.grep('VID:PID=2E8A:0005'):UI elements for boardsards(self):
                    messagebox.showerror(f"Error - Board {i+1}", message)        results.append([port_info.device, port_info.serial_number])        self.create_board_frames()    """Detect connected XIAO RP2040 boards"""    # Detect connected boards
                
            self.status_var.set(f"All fans turned ON at {speed}% on {success_count}/{len(self.boards)} boards")
    tage(self, value):n as e:end([port_info.device, port_info.serial_number])
    def apply_fan_settings(self):t entry is a valid percentage (0-100)"""showerror("Error Scanning Boards", str(e))ted_boards:
        """Apply the fan speed to all boards"""alue == "":self.status_var.set(f"Error: {str(e)}")agebox.showwarning("No Boards Found", "No XIAO RP2040 boards were detected.")
        if not self.boards:ds found")
            messagebox.showwarning("No Boards", "No boards available to control fans.")00)"""
            returne)d XIAO RP2040 boards"""
            val <= 100nnections
        try:    except ValueError:    for port_info in list_ports.grep('VID:PID=2E8A:0005'):    try:        for port, serial_number in detected_boards:
            speed = int(self.fan_speed_var.get())ce, port_info.serial_number])rdConnection(port, serial_number))
        except ValueError:
            messagebox.showerror("Invalid Value", "Please enter a valid fan speed (0-100%).")s(self):
            returngs to all boards"""tage(self, value):e_board_frames()
            success_count = 0"""Validate that entry is a valid percentage (0-100)"""
        success_count = 0
        for i, board in enumerate(self.boards):settings to all boards"""xception as e:
            success, message = board.set_fan_speed(speed)
            if success:
                success_count += 1ettings(i)
                # Update the fans_on flag if needed 1))::
                if speed > 0 and not self.fans_on:    except Exception as e:    return False    try:"""Detect connected XIAO RP2040 boards"""
                    self.fans_on = True
                    self.fan_button_var.set("Turn Fans OFF")    def apply_all_settings(self):            success_count += 1    for port_info in list_ports.grep('VID:PID=2E8A:0005'):
                elif speed == 0 and self.fans_on:Applied settings to {success_count} board(s), {error_count} error(s)")ll boards - optimized with status updates"""s e:rt_info.device, port_info.serial_number])
                    self.fans_on = False
                    self.fan_button_var.set("Turn Fans ON")f):
            else:
                messagebox.showerror(f"Error - Board {i+1}", message)f.boards:s_var.set("Applying settings to all boards...")s a valid percentage (0-100)"""
                messagebox.showwarning("No Boards", "No boards available to control fans.").root.update_idletasks()  # Update UI immediatelyle_all_fans(self):alue == "":
        self.status_var.set(f"Fan speed set to {speed}% on {success_count}/{len(self.boards)} boards")ards"""
    
    def export_settings(self):No boards available to control fans.")
        """Export current LED settings and schedules to a text file"""
        if not self.boards:self.fans_on = Falseocess boards sequentially to avoid overloading serial connectionspt ValueError:
            messagebox.showwarning("No Boards", "No boards available to export settings from.")ar.set("Turn Fans ON")lf.boards)):
            return
            
        try: enumerate(self.boards):_var.set(f"Applying settings to board {i+1}...")_var.set("Turn Fans ON")o all boards"""
            # Collect all settingsrd.turn_fan_off()ngs(i)
            settings = {}ccess:ss_count += 1ount = 0 0
            for board_idx in range(len(self.boards)):
                board_settings = {"intensity": {}, "schedule": {}, "fan": {}}else:error_count += 1success, message = board.turn_fan_off() range(len(self.boards)):
                
                # Get intensity settings    .status_var.set(f"Applied settings to {success_count} board(s), {error_count} error(s)")        success_count += 1    self.apply_board_settings(i)
                for channel_name in LED_CHANNELS:elf.status_var.set(f"All fans turned OFF on {success_count}/{len(self.boards)} boards")  success_count += 1
                    try: - Board {i+1}", message)s e:
                        value = int(self.led_entries[(board_idx, channel_name)].get())s"""
                        board_settings["intensity"][channel_name] = value)
                    except (ValueError, KeyError):self.fans_on = Truemessagebox.showwarning("No Boards", "No boards available to control fans.").status_var.set(f"Applied settings to {success_count} board(s), {error_count} error(s)")
                        board_settings["intensity"][channel_name] = 0Fans OFF")
                l fansll_fans(self):
                # Get board-level schedule settings
                if board_idx in self.board_schedules:ans OFF")
                    board_settings["schedule"] = {
                        "on_time": self.board_schedules[board_idx].get("on_time", "08:00"),
                        "off_time": self.board_schedules[board_idx].get("off_time", "20:00"),speed = 50  # Default to 50% if invalid
                        "enabled": self.board_schedules[board_idx].get("enabled", False)d_var.set("50")elf.fan_speed_var.get())
                    }
                
                # Add fan settings enumerate(self.boards):eed_var.set("50")_var.set("Turn Fans ON")
                board_settings["fan"] = {rd.set_fan_speed(speed)
                    "enabled": self.boards[board_idx].fan_enabled,ccess:ount = 0ount = 0
                    "speed": self.boards[board_idx].fan_speed
                }else:success, message = board.set_fan_speed(speed)success, message = board.turn_fan_off()
                
                settings[f"board_{board_idx+1}"] = board_settings                                    success_count += 1                success_count += 1
            "All fans turned ON at {speed}% on {success_count}/{len(self.boards)} boards")
            # Get file path from user
            file_path = filedialog.asksaveasfilename(self):True
                defaultextension=".json",
                filetypes=[("JSON files", "*.json"), ("Text files", "*.txt"), ("All files", "*.*")],f.boards:
                title="Save LED Settings"messagebox.showwarning("No Boards", "No boards available to control fans.")# Get the speed from the entryy_fan_settings(self)::
            )returntry:pply the fan speed to all boards"""# Turn on all fans
            
            if not file_path:No Boards", "No boards available to control fans.")n_var.set("Turn Fans OFF")
                return  # User canceled
                ueError:lf.fan_speed_var.set("50")eed from the entry
            # Save to filemessagebox.showerror("Invalid Value", "Please enter a valid fan speed (0-100%).")    try:
            with open(file_path, 'w') as f:.fan_speed_var.get())nt(self.fan_speed_var.get())
                json.dump(settings, f, indent=4)
                100%).")
            self.status_var.set(f"Settings exported to {file_path}") enumerate(self.boards):ess:ed_var.set("50")
            messagebox.showinfo("Export Successful", f"Settings successfully exported to {file_path}")rd.set_fan_speed(speed)+= 1
            
        except Exception as e:essage)
            messagebox.showerror("Export Error", f"Error exporting settings: {str(e)}")ag if needed_fan_speed(speed)
            self.status_var.set(f"Export error: {str(e)}")uccess_count}/{len(self.boards)} boards")
    
    def import_settings(self):set("Turn Fans OFF")eded
        """Import LED settings and schedules from a text file and apply them"""
        try:   self.fans_on = Falself.boards:   self.fans_on = True
            # Get file path from user fans.")ount}/{len(self.boards)} boards")
            file_path = filedialog.askopenfilename(:rnelif speed == 0 and self.fans_on:
                filetypes=[("JSON files", "*.json"), ("Text files", "*.txt"), ("All files", "*.*")],
                title="Import LED Settings"                try:                self.fan_button_var.set("Turn Fans ON")    """Apply the fan speed to all boards"""
            )Fan speed set to {speed}% on {success_count}/{len(self.boards)} boards")an_speed_var.get())
            
            if not file_path:f):error("Invalid Value", "Please enter a valid fan speed (0-100%).")
                return  # User canceled
                f.boards:
            # Read settings from filemessagebox.showwarning("No Boards", "No boards available to export settings from.")ess_count = 0rt_settings(self):speed = int(self.fan_speed_var.get())
            with open(file_path, 'r') as f:returni, board in enumerate(self.boards):xport current LED settings and schedules to a text file"""pt ValueError:
                settings = json.load(f)) Value", "Please enter a valid fan speed (0-100%).")
            oards", "No boards available to export settings from.")
            # Validate imported data format
            if not isinstance(settings, dict):
                raise ValueError("Invalid settings file format")board_idx in range(len(self.boards)):if speed > 0 and not self.fans_on: in enumerate(self.boards):
                sity": {}, "schedule": {}, "fan": {}}eed(speed)
            # Make sure we have boards to apply settings to
            if not self.boards:tensity settingsed == 0 and self.fans_on:x in range(len(self.boards)):count += 1
                messagebox.showwarning("No Boards", "No boards connected to apply settings to.")
                return
                es[(board_idx, channel_name)].get())
            # Apply settings to GUI entriesalue
            applied_count = 0    except (ValueError, KeyError):    try:elif speed == 0 and self.fans_on:
            fan_settings_found = False[channel_name] = 0ed}% on {success_count}/{len(self.boards)} boards")s[(board_idx, channel_name)].get())
            
            for board_key, board_settings in settings.items():s
                try:
                    # Extract board index (format: "board_X")
                    board_idx = int(board_key.split("_")[1]) - 1"),from.")
                       "off_time": self.board_schedules[board_idx].get("off_time", "20:00"),_idx in self.board_schedules:
                    if board_idx < 0 or board_idx >= len(self.boards):        "enabled": self.board_schedules[board_idx].get("enabled", False)board_settings["schedule"] = {ettings(self):
                        continue  # Skip if board index is out of ranget("on_time", "08:00"),ings and schedules to a text file"""
                    off_time", "20:00"),
                    # Apply intensity settings
                    if "intensity" in board_settings:
                        for channel_name, value in board_settings["intensity"].items():   "enabled": self.boards[board_idx].fan_enabled,oard_settings = {"intensity": {}, "schedule": {}, "fan": {}}
                            if channel_name in LED_CHANNELS and (board_idx, channel_name) in self.led_entries:    "speed": self.boards[board_idx].fan_speed# Add fan settings
                                self.led_entries[(board_idx, channel_name)].delete(0, tk.END)
                                self.led_entries[(board_idx, channel_name)].insert(0, str(value))        for channel_name in LED_CHANNELS:        "enabled": self.boards[board_idx].fan_enabled,settings = {}
                                applied_count += 1rd_idx+1}"] = board_settings].fan_speedn(self.boards)):
                    
                    # Apply schedule settingssity"][channel_name] = value
                    if "schedule" in board_settings:
                        schedule = board_settings["schedule"]ntensity"][channel_name] = 0
                           filetypes=[("JSON files", "*.json"), ("Text files", "*.txt"), ("All files", "*.*")],    Get file path from user       try:
                        # Update time entries    title="Save LED Settings"    # Get board-level schedule settingsfile_path = filedialog.asksaveasfilename(            value = int(self.led_entries[(board_idx, channel_name)].get())
                        if "on_time" in schedule and (board_idx, "on") in self.board_time_entries:hedules:ion=".json",_settings["intensity"][channel_name] = value
                            self.board_time_entries[(board_idx, "on")].delete(0, tk.END)Text files", "*.txt"), ("All files", "*.*")], KeyError):
                            self.board_time_entries[(board_idx, "on")].insert(0, schedule["on_time"])ot file_path:        "on_time": self.board_schedules[board_idx].get("on_time", "08:00"),title="Save LED Settings"        board_settings["intensity"][channel_name] = 0
                        User canceledff_time": self.board_schedules[board_idx].get("off_time", "20:00"),
                        if "off_time" in schedule and (board_idx, "off") in self.board_time_entries:("enabled", False)
                            self.board_time_entries[(board_idx, "off")].delete(0, tk.END)
                            self.board_time_entries[(board_idx, "off")].insert(0, schedule["off_time"]) open(file_path, 'w') as f:return  # User canceled    board_settings["schedule"] = {
                        
                        # Update checkbox
                        if "enabled" in schedule and board_idx in self.board_schedule_vars:self.status_var.set(f"Settings exported to {file_path}")        "enabled": self.boards[board_idx].fan_enabled,with open(file_path, 'w') as f:            "enabled": self.board_schedules[board_idx].get("enabled", False)
                            self.board_schedule_vars[board_idx].set(schedule["enabled"])o("Export Successful", f"Settings successfully exported to {file_path}")elf.boards[board_idx].fan_speedings, f, indent=4)
                        
                        # Update internal schedule data
                        if board_idx in self.board_schedules:        messagebox.showerror("Export Error", f"Error exporting settings: {str(e)}")            settings[f"board_{board_idx+1}"] = board_settings        messagebox.showinfo("Export Successful", f"Settings successfully exported to {file_path}")            board_settings["fan"] = {
                            self.board_schedules[board_idx].update({t(f"Export error: {str(e)}")
                                "on_time": schedule.get("on_time", "08:00"),
                                "off_time": schedule.get("off_time", "20:00"),rt_settings(self):file_path = filedialog.asksaveasfilename(messagebox.showerror("Export Error", f"Error exporting settings: {str(e)}")    }
                                "enabled": schedule.get("enabled", False)hedules from a text file and apply them"""on",ort error: {str(e)}")
                            }), "*.*")],
                        
                        applied_count += 1ilename(hem"""
                       filetypes=[("JSON files", "*.json"), ("Text files", "*.txt"), ("All files", "*.*")],e_path = filedialog.asksaveasfilename(
                    # Apply fan settings if present    title="Import LED Settings"if not file_path:# Get file path from user    defaultextension=".json",
                    if "fan" in board_settings:askopenfilename(JSON files", "*.json"), ("Text files", "*.txt"), ("All files", "*.*")],
                        fan = board_settings["fan"]", "*.*")],s"
                        fan_settings_found = Trueot file_path:ve to filetitle="Import LED Settings"
                        ed as f:
                        # Only set the fan speed in the UI for the first board with settings
                        if fan_settings_found and board_idx == 0:
                            self.fan_speed_var.set(str(fan.get("speed", 50)))with open(file_path, 'r') as f:self.status_var.set(f"Settings exported to {file_path}")    return  # User canceled    
                            ful", f"Settings successfully exported to {file_path}")
                            # Update fan button state
                            if fan.get("enabled", False):
                                self.fans_on = Trueot isinstance(settings, dict):agebox.showerror("Export Error", f"Error exporting settings: {str(e)}")settings = json.load(f)
                                self.fan_button_var.set("Turn Fans OFF")mat")
                            else:gs successfully exported to {file_path}")
                                self.fans_on = False
                                self.fan_button_var.set("Turn Fans ON")f.boards:settings and schedules from a text file and apply them"""ValueError("Invalid settings file format")on as e:
                        messagebox.showwarning("No Boards", "No boards connected to apply settings to.")ox.showerror("Export Error", f"Error exporting settings: {str(e)}")
                        applied_count += 1)}")
                            filename(s:
                except (ValueError, IndexError, KeyError):triess", "*.json"), ("Text files", "*.txt"), ("All files", "*.*")],("No Boards", "No boards connected to apply settings to.")
                    continue  # Skip invalid entriesapplied_count = 0    title="Import LED Settings"    returnmport LED settings and schedules from a text file and apply them"""
            
            self.status_var.set(f"Imported settings from {file_path}")entriesle path from user
            :
            if messagebox.askyesno("Apply Settings", 
                                 f"Successfully loaded {applied_count} settings from file.\n\nDo you want to apply these settings to the boards now?"):# Extract board index (format: "board_X")D Settings"
                self.apply_all_settings()
                
                # Also apply fan settings if they were foundif board_idx < 0 or board_idx >= len(self.boards):ings = json.load(f)# Extract board index (format: "board_X")ile_path:
                if fan_settings_found:ard index is out of range
                    self.apply_fan_settings()
                
        except Exception as e:
            messagebox.showerror("Import Error", f"Error importing settings: {str(e)}")
            self.status_var.set(f"Import error: {str(e)}").led_entries:
board_idx, channel_name)].delete(0, tk.END)
            self.led_entries[(board_idx, channel_name)].insert(0, str(value))agebox.showwarning("No Boards", "No boards connected to apply settings to.")    for channel_name, value in board_settings["intensity"].items():sinstance(settings, dict):
if __name__ == "__main__": += 1rd_idx, channel_name) in self.led_entries:tings file format")
    root = tk.Tk()
    app = LEDControlGUI(root)))
    root.mainloop()schedule" in board_settings:t = 0        applied_count += 1boards:












                        if "enabled" in schedule and board_idx in self.board_schedule_vars:
                            self.board_schedule_vars[board_idx].set(schedule["enabled"])
                        
                        # Update internal schedule data
                        if board_idx in self.board_schedules:
                            self.board_schedules[board_idx].update({
                                "on_time": schedule.get("on_time", "08:00"),
                                "off_time": schedule.get("off_time", "20:00"),
                                "enabled": schedule.get("enabled", False)
                            })
                           app = LEDControlGUI(root)    root = tk.Tk()if __name__ == "__main__":            self.status_var.set(f"Import error: {str(e)}")            messagebox.showerror("Import Error", f"Error importing settings: {str(e)}")        except Exception as e:                                    self.apply_fan_settings()                if fan_settings_found:                # Also apply fan settings if they were found                                self.apply_all_settings()                                 f"Successfully loaded {applied_count} settings from file.\n\nDo you want to apply these settings to the boards now?"):            if messagebox.askyesno("Apply Settings",                         self.status_var.set(f"Imported settings from {file_path}")                                continue  # Skip invalid entries
                        applied_count += 1
                    
                    # Apply fan settings if present
                    if "fan" in board_settings:
                        fan = board_settings["fan"]pp = LEDControlGUI(root)    root = tk.Tk()if __name__ == "__main__":                                self.fan_button_var.set("Turn Fans ON")
                        fan_settings_found = True
                        
                        # Only set the fan speed in the UI for the first board with settings
                        if fan_settings_found and board_idx == 0:eError, IndexError, KeyError):
                            self.fan_speed_var.set(str(fan.get("speed", 50)))
                            
                            # Update fan button state from {file_path}")
                            if fan.get("enabled", False):
                                self.fans_on = Trueo("Apply Settings", 
                                self.fan_button_var.set("Turn Fans OFF")ed {applied_count} settings from file.\n\nDo you want to apply these settings to the boards now?"):
                            else:
                                self.fans_on = False
                                self.fan_button_var.set("Turn Fans ON")if they were found
                        ngs_found:
                        applied_count += 1
                            
                except (ValueError, IndexError, KeyError):pt Exception as e:
                    continue  # Skip invalid entriestings: {str(e)}")
            self.status_var.set(f"Import error: {str(e)}")
            self.status_var.set(f"Imported settings from {file_path}")
            
            if messagebox.askyesno("Apply Settings", 
                                 f"Successfully loaded {applied_count} settings from file.\n\nDo you want to apply these settings to the boards now?"):()
                self.apply_all_settings()
                
                # Also apply fan settings if they were found                if fan_settings_found:                    self.apply_fan_settings()                        except Exception as e:            messagebox.showerror("Import Error", f"Error importing settings: {str(e)}")            self.status_var.set(f"Import error: {str(e)}")if __name__ == "__main__":    root = tk.Tk()    app = LEDControlGUI(root)
    root.mainloop()
