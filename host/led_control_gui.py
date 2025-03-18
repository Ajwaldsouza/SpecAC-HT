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
        
    def connect(self):
        """Establish serial connection to the board"""
        try:
            self.serial_conn = serial.Serial(
                port=self.port,
                baudrate=115200,
                timeout=1
            )
            self.is_connected = True
            return True
        except serial.SerialException as e:
            self.last_error = str(e)
            self.is_connected = False
            return False
            
    def disconnect(self):
        """Close serial connection"""
        if self.serial_conn and self.is_connected:
            try:
                self.serial_conn.close()
            except:
                pass
            finally:
                self.is_connected = False
    
    def send_command(self, duty_values):
        """Send command to update LED brightness"""
        if not self.is_connected:
            if not self.connect():
                return False, self.last_error
                
        try:
            # Format: "SETALL d0 d1 d2 d3 d4 d5\n"
            command = "SETALL"
            for val in duty_values:
                command += f" {val}"
            command += "\n"
            
            self.serial_conn.write(command.encode('utf-8'))
            response = self.serial_conn.readline().decode('utf-8').strip()
            
            if response == "OK":
                return True, "Success"
            else:
                return False, f"Unexpected response: {response}"
        except Exception as e:
            self.is_connected = False
            return False, str(e)


class LEDControlGUI:
    """Main GUI application for controlling LED brightness"""
    
    def __init__(self, root):
        self.root = root
        self.root.title("LED Control System")
        self.root.geometry("1200x800")
        
        self.style = ttk.Style()
        self.style.theme_use('clam')  # Modern theme
        
        self.boards = []
        self.board_frames = []
        self.led_entries = {}  # {(board_idx, channel): entry_widget}
        
        # Track master light state
        self.master_on = True
        self.saved_values = {}  # To store values when turning off
        
        # Scheduling related variables - now at board level instead of channel level
        self.board_schedules = {}  # {board_idx: {"on_time": time, "off_time": time, "enabled": bool}}
        self.board_time_entries = {}   # {board_idx, "on"/"off"): entry_widget}
        self.board_schedule_vars = {}  # {board_idx: BooleanVar}
        self.scheduler_running = False
        self.scheduler_thread = None
        
        self.create_gui()
        self.start_scheduler()
        
    def create_gui(self):
        """Create the main GUI layout"""
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(column=0, row=0, sticky=(tk.N, tk.W, tk.E, tk.S))
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        
        # Header
        header_frame = ttk.Frame(main_frame)
        header_frame.grid(column=0, row=0, columnspan=2, sticky=(tk.W, tk.E))
        
        ttk.Label(header_frame, text="LED Control System", font=('Helvetica', 16, 'bold')).pack(side=tk.LEFT)
        
        # Master lights control button
        self.master_button_var = tk.StringVar(value="All Lights OFF")
        master_button = ttk.Button(
            header_frame,
            textvariable=self.master_button_var,
            command=self.toggle_all_lights,
            width=15
        )
        master_button.pack(side=tk.LEFT, padx=20)
        
        # Add scheduler control button
        self.scheduler_button_var = tk.StringVar(value="Start Scheduler")
        scheduler_button = ttk.Button(
            header_frame,
            textvariable=self.scheduler_button_var,
            command=self.toggle_scheduler,
            width=15
        )
        scheduler_button.pack(side=tk.LEFT, padx=10)
        
        # Scan and Apply buttons
        btn_frame = ttk.Frame(header_frame)
        btn_frame.pack(side=tk.RIGHT)
        
        ttk.Button(btn_frame, text="Scan for Boards", command=self.scan_boards).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Apply All Settings", command=self.apply_all_settings).pack(side=tk.LEFT, padx=5)
        
        # Scrollable area for board frames
        canvas_frame = ttk.Frame(main_frame)
        canvas_frame.grid(column=0, row=1, sticky=(tk.N, tk.W, tk.E, tk.S))
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(1, weight=1)
        
        canvas = tk.Canvas(canvas_frame)
        scrollbar = ttk.Scrollbar(canvas_frame, orient=tk.HORIZONTAL, command=canvas.xview)
        scrollable_frame = ttk.Frame(canvas)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(
                scrollregion=canvas.bbox("all")
            )
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor=tk.NW)
        canvas.configure(xscrollcommand=scrollbar.set)
        
        canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.BOTTOM, fill=tk.X)
        
        # Bottom frame for Import/Export buttons (moved from header)
        bottom_frame = ttk.Frame(main_frame)
        bottom_frame.grid(column=0, row=2, columnspan=2, sticky=(tk.W, tk.E))
        
        ttk.Button(bottom_frame, text="Export Settings", command=self.export_settings).pack(side=tk.LEFT, padx=5)
        ttk.Button(bottom_frame, text="Import Settings", command=self.import_settings).pack(side=tk.RIGHT, padx=5)
        
        # Status bar (now at row 3 instead of row 2)
        self.status_var = tk.StringVar(value="Ready")
        status_bar = ttk.Label(main_frame, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.grid(column=0, row=3, columnspan=2, sticky=(tk.W, tk.E))
        
        self.scrollable_frame = scrollable_frame
        self.scan_boards()
        
    def toggle_all_lights(self):
        """Toggle all lights on or off on all boards"""
        if not self.boards:
            messagebox.showwarning("No Boards", "No boards available to control.")
            return
        
        if self.master_on:
            # Turn OFF all lights - save current values first
            self.master_on = False
            self.master_button_var.set("All Lights ON")
            
            # Save current values
            self.saved_values = {}
            for board_idx in range(len(self.boards)):
                for channel_name in LED_CHANNELS:
                    key = (board_idx, channel_name)
                    if key in self.led_entries:
                        try:
                            self.saved_values[key] = self.led_entries[key].get()
                            # Set entry to 0
                            self.led_entries[key].delete(0, tk.END)
                            self.led_entries[key].insert(0, "0")
                        except (ValueError, KeyError):
                            pass
            
            # Apply the zeros to all boards
            self.apply_all_settings()
            self.status_var.set("All lights turned OFF")
            
        else:
            # Turn ON all lights - restore saved values
            self.master_on = True
            self.master_button_var.set("All Lights OFF")
            
            # Restore saved values
            for key, value in self.saved_values.items():
                if key in self.led_entries:
                    board_idx, channel_name = key
                    self.led_entries[key].delete(0, tk.END)
                    self.led_entries[key].insert(0, value)
            
            # Apply the restored values
            self.apply_all_settings()
            self.status_var.set("All lights restored to previous settings")
    
    def toggle_scheduler(self):
        """Enable or disable the scheduler"""
        if self.scheduler_running:
            self.scheduler_running = False
            self.scheduler_button_var.set("Start Scheduler")
            self.status_var.set("Scheduler stopped")
        else:
            self.scheduler_running = True
            self.scheduler_button_var.set("Stop Scheduler")
            self.status_var.set("Scheduler started")
            if not self.scheduler_thread or not self.scheduler_thread.is_alive():
                self.start_scheduler()
    
    def start_scheduler(self):
        """Start the scheduler thread"""
        self.scheduler_running = True
        self.scheduler_button_var.set("Stop Scheduler")
        self.scheduler_thread = threading.Thread(target=self.schedule_checker, daemon=True)
        self.scheduler_thread.start()
    
    def is_time_between(self, current_time, start_time, end_time):
        """Check if current time is between start and end times, handling overnight periods"""
        # Convert all times to datetime for comparison
        current = datetime.strptime(current_time, "%H:%M")
        start = datetime.strptime(start_time, "%H:%M")
        end = datetime.strptime(end_time, "%H:%M")
        
        if start <= end:
            # Simple case: start time is before end time (e.g., 08:00 to 20:00)
            return start <= current <= end
        else:
            # Wrap-around case: end time is before start time (e.g., 20:00 to 08:00 next day)
            return current >= start or current <= end
    
    def apply_board_settings(self, board_idx):
        """Apply settings for a specific board"""
        if board_idx >= len(self.boards):
            messagebox.showerror("Error", "Invalid board index")
            return
            
        board = self.boards[board_idx]
        duty_values = []
        
        # Check if scheduling is enabled for this board
        scheduling_enabled = False
        if board_idx in self.board_schedules:
            scheduling_enabled = self.board_schedules[board_idx].get("enabled", False)
        
        if scheduling_enabled:
            # Get the current time
            current_time = datetime.now().strftime("%H:%M")
            on_time = self.board_schedules[board_idx].get("on_time", "08:00")
            off_time = self.board_schedules[board_idx].get("off_time", "20:00")
            
            # Check if current time is within the ON period
            if not self.is_time_between(current_time, on_time, off_time):
                # Outside of ON period - save the settings but turn off LEDs
                saved_values = {}
                
                # Save the current UI values for later use
                for channel in LED_CHANNELS:
                    try:
                        saved_values[channel] = self.led_entries[(board_idx, channel)].get()
                    except (ValueError, KeyError):
                        saved_values[channel] = "0"
                
                # Store saved values
                self.board_schedules[board_idx]["saved_values"] = saved_values
                
                # Send all zeros to the board (lights off)
                success, message = board.send_command([0, 0, 0, 0, 0, 0])
                
                if success:
                    self.status_var.set(f"Board {board_idx+1}: Settings saved, lights off (outside scheduled hours)")
                else:
                    messagebox.showerror(f"Error - Board {board_idx+1}", message)
                    self.status_var.set(f"Board {board_idx+1}: Error - {message}")
                    
                return
        
        # Get duty cycle values for each channel
        for channel in LED_CHANNELS:
            try:
                percentage = int(self.led_entries[(board_idx, channel)].get())
                duty = int((percentage / 100.0) * 4095)
                duty_values.append(duty)
            except ValueError:
                duty_values.append(0)
        
        # If scheduling is enabled, save these values for future use
        if scheduling_enabled:
            saved_values = {}
            for channel in LED_CHANNELS:
                try:
                    saved_values[channel] = self.led_entries[(board_idx, channel)].get()
                except:
                    saved_values[channel] = "0"
            self.board_schedules[board_idx]["saved_values"] = saved_values
        
        # Send command to the board
        success, message = board.send_command(duty_values)
        
        if success:
            if scheduling_enabled:
                self.status_var.set(f"Board {board_idx+1}: Settings applied (within scheduled hours)")
            else:
                self.status_var.set(f"Board {board_idx+1}: Settings applied successfully")
        else:
            messagebox.showerror(f"Error - Board {board_idx+1}", message)
            self.status_var.set(f"Board {board_idx+1}: Error - {message}")
    
    def update_board_schedule(self, board_idx):
        """Update the schedule for a specific board"""
        if board_idx not in self.board_schedules:
            self.board_schedules[board_idx] = {"enabled": False}
            
        # Get current values from widgets
        self.board_schedules[board_idx]["enabled"] = self.board_schedule_vars[board_idx].get()
        
        if (board_idx, "on") in self.board_time_entries:
            self.board_schedules[board_idx]["on_time"] = self.board_time_entries[(board_idx, "on")].get()
            
        if (board_idx, "off") in self.board_time_entries:
            self.board_schedules[board_idx]["off_time"] = self.board_time_entries[(board_idx, "off")].get()
            
        # Store current LED values
        saved_values = {}
        for channel_name in LED_CHANNELS:
            key = (board_idx, channel_name)
            if key in self.led_entries:
                try:
                    saved_values[channel_name] = self.led_entries[key].get()
                except:
                    saved_values[channel_name] = "0"
        
        self.board_schedules[board_idx]["saved_values"] = saved_values
        
        # If scheduling was just enabled, check if we need to turn off lights
        if self.board_schedules[board_idx]["enabled"]:
            current_time = datetime.now().strftime("%H:%M")
            on_time = self.board_schedules[board_idx].get("on_time", "08:00")
            off_time = self.board_schedules[board_idx].get("off_time", "20:00")
            
            if not self.is_time_between(current_time, on_time, off_time):
                # Outside of ON period - we should turn off the LEDs
                self.status_var.set(f"Board {board_idx+1}: Schedule enabled, outside ON hours - lights off")
                
                # Set all entries to 0 in the UI
                for channel_name in LED_CHANNELS:
                    key = (board_idx, channel_name)
                    if key in self.led_entries:
                        self.led_entries[key].delete(0, tk.END)
                        self.led_entries[key].insert(0, "0")
                
                # Apply the settings (which will send zeros to the board)
                self.apply_board_settings(board_idx)
    
    def schedule_checker(self):
        """Background thread to check and apply scheduled settings"""
        while True:
            if not self.scheduler_running:
                time.sleep(1)
                continue
                
            current_time = datetime.now().strftime("%H:%M")
            changes_made = False
            
            for board_idx, schedule_info in self.board_schedules.items():
                if not schedule_info.get("enabled", False):
                    continue
                    
                on_time = schedule_info.get("on_time", "")
                off_time = schedule_info.get("off_time", "")
                
                if on_time == current_time:
                    # Time to turn on all LEDs on this board
                    if "saved_values" in schedule_info and schedule_info["saved_values"]:
                        for channel_name, value in schedule_info["saved_values"].items():
                            key = (board_idx, channel_name)
                            if key in self.led_entries:
                                self.led_entries[key].delete(0, tk.END)
                                self.led_entries[key].insert(0, value)
                        changes_made = True
                        self.status_var.set(f"Board {board_idx+1}: Schedule activated - turning ON")
                    
                elif off_time == current_time:
                    # Time to turn off all LEDs on this board
                    # Save current values for all channels before turning off
                    saved_values = {}
                    for channel_name in LED_CHANNELS:
                        key = (board_idx, channel_name)
                        if key in self.led_entries:
                            saved_values[channel_name] = self.led_entries[key].get()
                            self.led_entries[key].delete(0, tk.END)
                            self.led_entries[key].insert(0, "0")
                    
                    schedule_info["saved_values"] = saved_values
                    changes_made = True
                    self.status_var.set(f"Board {board_idx+1}: Schedule activated - turning OFF")
            
            # Apply changes if any were made
            if changes_made:
                self.root.after(0, self.apply_all_settings)
                
            time.sleep(10)  # Check every 10 seconds
    
    def scan_boards(self):
        """Detect and initialize connections to XIAO RP2040 boards"""
        # Clear previous boards and GUI elements
        for board in self.boards:
            board.disconnect()
        self.boards = []
        
        for frame in self.board_frames:
            frame.destroy()
        self.board_frames = []
        self.led_entries = {}
        
        # Reset master button state
        self.master_on = True
        self.master_button_var.set("All Lights OFF")
        self.saved_values = {}
        
        # Detect connected boards
        try:
            detected_boards = self.detect_xiao_boards()
            
            if not detected_boards:
                messagebox.showwarning("No Boards Found", "No XIAO RP2040 boards were detected.")
                self.status_var.set("No boards found")
                return
                
            # Create board connections
            for port, serial_number in detected_boards:
                self.boards.append(BoardConnection(port, serial_number))
            
            # Create GUI elements for boards
            self.create_board_frames()
            
            self.status_var.set(f"Found {len(self.boards)} board(s)")
        except Exception as e:
            messagebox.showerror("Error Scanning Boards", str(e))
            self.status_var.set(f"Error: {str(e)}")
    
    def detect_xiao_boards(self):
        """Detect connected XIAO RP2040 boards"""
        results = []
        for port_info in list_ports.grep('VID:PID=2E8A:0005'):
            results.append([port_info.device, port_info.serial_number])
        return results
    
    def create_board_frames(self):
        """Create frames for each detected board"""
        for i, board in enumerate(self.boards):
            frame = ttk.LabelFrame(self.scrollable_frame, text=f"Board {i+1}: {board.serial_number}")
            frame.grid(column=i, row=0, padx=10, pady=10)
            self.board_frames.append(frame)
            
            # LED control section
            led_control_frame = ttk.LabelFrame(frame, text="LED Controls")
            led_control_frame.grid(column=0, row=0, padx=5, pady=5, sticky=(tk.W, tk.E))
            
            # Add header row for LED controls
            ttk.Label(led_control_frame, text="LED Channel").grid(column=1, row=0, sticky=tk.W, padx=5)
            ttk.Label(led_control_frame, text="Intensity (%)").grid(column=2, row=0, sticky=tk.W, padx=5)
            
            # Add LED controls for each channel
            for row, (channel_name, channel_idx) in enumerate(LED_CHANNELS.items(), start=1):
                color_frame = ttk.Frame(led_control_frame, width=20, height=20)
                color_frame.grid(column=0, row=row, padx=5, pady=2)
                color_label = tk.Label(color_frame, bg=LED_COLORS[channel_name], width=2)
                color_label.pack(fill=tk.BOTH, expand=True)
                
                ttk.Label(led_control_frame, text=channel_name).grid(column=1, row=row, sticky=tk.W, padx=5)
                
                value_var = tk.StringVar(value="0")
                entry = ttk.Spinbox(
                    led_control_frame, 
                    from_=0, 
                    to=100, 
                    width=5, 
                    textvariable=value_var,
                    validate='key',
                    validatecommand=(self.root.register(self.validate_percentage), '%P')
                )
                entry.grid(column=2, row=row, sticky=tk.W, padx=5)
                ttk.Label(led_control_frame, text="%").grid(column=3, row=row, sticky=tk.W)
                
                self.led_entries[(i, channel_name)] = entry
            
            # Scheduling section - one per board
            schedule_frame = ttk.LabelFrame(frame, text="Board Schedule")
            schedule_frame.grid(column=0, row=1, padx=5, pady=5, sticky=(tk.W, tk.E))
            
            # Create schedule controls
            ttk.Label(schedule_frame, text="ON Time:").grid(column=0, row=0, padx=5, pady=5, sticky=tk.W)
            on_time_var = tk.StringVar(value="08:00")
            on_time = ttk.Entry(schedule_frame, width=7, textvariable=on_time_var)
            on_time.grid(column=1, row=0, padx=5, pady=5)
            self.board_time_entries[(i, "on")] = on_time
            
            ttk.Label(schedule_frame, text="OFF Time:").grid(column=2, row=0, padx=5, pady=5, sticky=tk.W)
            off_time_var = tk.StringVar(value="20:00")
            off_time = ttk.Entry(schedule_frame, width=7, textvariable=off_time_var)
            off_time.grid(column=3, row=0, padx=5, pady=5)
            self.board_time_entries[(i, "off")] = off_time
            
            # Schedule enable checkbox
            schedule_var = tk.BooleanVar(value=False)
            schedule_check = ttk.Checkbutton(
                schedule_frame, 
                text="Enable Scheduling",
                variable=schedule_var,
                command=lambda b_idx=i: self.update_board_schedule(b_idx)
            )
            schedule_check.grid(column=4, row=0, padx=10, pady=5, sticky=tk.W)
            self.board_schedule_vars[i] = schedule_var
            
            # Initialize the schedule data for this board
            self.board_schedules[i] = {
                "on_time": "08:00",
                "off_time": "20:00",
                "enabled": False,
                "saved_values": {}
            }
                
            # Individual apply button
            ttk.Button(
                frame, 
                text="Apply", 
                command=lambda b_idx=i: self.apply_board_settings(b_idx)
            ).grid(column=0, row=2, pady=10, sticky=(tk.W, tk.E))
    
    def validate_percentage(self, value):
        """Validate that entry is a valid percentage (0-100)"""
        if value == "":
            return True
        try:
            val = int(value)
            return 0 <= val <= 100
        except ValueError:
            return False
    
    def apply_all_settings(self):
        """Apply settings to all boards"""
        success_count = 0
        error_count = 0
        
        for i in range(len(self.boards)):
            try:
                self.apply_board_settings(i)
                success_count += 1
            except Exception as e:
                error_count += 1
        
        self.status_var.set(f"Applied settings to {success_count} board(s), {error_count} error(s)")
    
    def export_settings(self):
        """Export current LED settings and schedules to a text file"""
        if not self.boards:
            messagebox.showwarning("No Boards", "No boards available to export settings from.")
            return
            
        try:
            # Collect all settings
            settings = {}
            for board_idx in range(len(self.boards)):
                board_settings = {"intensity": {}, "schedule": {}}
                
                # Get intensity settings
                for channel_name in LED_CHANNELS:
                    try:
                        value = int(self.led_entries[(board_idx, channel_name)].get())
                        board_settings["intensity"][channel_name] = value
                    except (ValueError, KeyError):
                        board_settings["intensity"][channel_name] = 0
                
                # Get board-level schedule settings
                if board_idx in self.board_schedules:
                    board_settings["schedule"] = {
                        "on_time": self.board_schedules[board_idx].get("on_time", "08:00"),
                        "off_time": self.board_schedules[board_idx].get("off_time", "20:00"),
                        "enabled": self.board_schedules[board_idx].get("enabled", False)
                    }
                
                settings[f"board_{board_idx+1}"] = board_settings
            
            # Get file path from user
            file_path = filedialog.asksaveasfilename(
                defaultextension=".json",
                filetypes=[("JSON files", "*.json"), ("Text files", "*.txt"), ("All files", "*.*")],
                title="Save LED Settings"
            )
            
            if not file_path:
                return  # User canceled
                
            # Save to file
            with open(file_path, 'w') as f:
                json.dump(settings, f, indent=4)
                
            self.status_var.set(f"Settings exported to {file_path}")
            messagebox.showinfo("Export Successful", f"Settings successfully exported to {file_path}")
            
        except Exception as e:
            messagebox.showerror("Export Error", f"Error exporting settings: {str(e)}")
            self.status_var.set(f"Export error: {str(e)}")
    
    def import_settings(self):
        """Import LED settings and schedules from a text file and apply them"""
        try:
            # Get file path from user
            file_path = filedialog.askopenfilename(
                filetypes=[("JSON files", "*.json"), ("Text files", "*.txt"), ("All files", "*.*")],
                title="Import LED Settings"
            )
            
            if not file_path:
                return  # User canceled
                
            # Read settings from file
            with open(file_path, 'r') as f:
                settings = json.load(f)
            
            # Validate imported data format
            if not isinstance(settings, dict):
                raise ValueError("Invalid settings file format")
                
            # Make sure we have boards to apply settings to
            if not self.boards:
                messagebox.showwarning("No Boards", "No boards connected to apply settings to.")
                return
                
            # Apply settings to GUI entries
            applied_count = 0
            for board_key, board_settings in settings.items():
                try:
                    # Extract board index (format: "board_X")
                    board_idx = int(board_key.split("_")[1]) - 1
                    
                    if board_idx < 0 or board_idx >= len(self.boards):
                        continue  # Skip if board index is out of range
                    
                    # Apply intensity settings
                    if "intensity" in board_settings:
                        for channel_name, value in board_settings["intensity"].items():
                            if channel_name in LED_CHANNELS and (board_idx, channel_name) in self.led_entries:
                                self.led_entries[(board_idx, channel_name)].delete(0, tk.END)
                                self.led_entries[(board_idx, channel_name)].insert(0, str(value))
                                applied_count += 1
                    
                    # Apply schedule settings
                    if "schedule" in board_settings:
                        schedule = board_settings["schedule"]
                        
                        # Update time entries
                        if "on_time" in schedule and (board_idx, "on") in self.board_time_entries:
                            self.board_time_entries[(board_idx, "on")].delete(0, tk.END)
                            self.board_time_entries[(board_idx, "on")].insert(0, schedule["on_time"])
                        
                        if "off_time" in schedule and (board_idx, "off") in self.board_time_entries:
                            self.board_time_entries[(board_idx, "off")].delete(0, tk.END)
                            self.board_time_entries[(board_idx, "off")].insert(0, schedule["off_time"])
                        
                        # Update checkbox
                        if "enabled" in schedule and board_idx in self.board_schedule_vars:
                            self.board_schedule_vars[board_idx].set(schedule["enabled"])
                        
                        # Update internal schedule data
                        if board_idx in self.board_schedules:
                            self.board_schedules[board_idx].update({
                                "on_time": schedule.get("on_time", "08:00"),
                                "off_time": schedule.get("off_time", "20:00"),
                                "enabled": schedule.get("enabled", False)
                            })
                        
                        applied_count += 1
                            
                except (ValueError, IndexError, KeyError):
                    continue  # Skip invalid entries
            
            self.status_var.set(f"Imported settings from {file_path}")
            
            if messagebox.askyesno("Apply Settings", 
                                 f"Successfully loaded {applied_count} settings from file.\n\nDo you want to apply these settings to the boards now?"):
                self.apply_all_settings()
                
        except Exception as e:
            messagebox.showerror("Import Error", f"Error importing settings: {str(e)}")
            self.status_var.set(f"Import error: {str(e)}")


if __name__ == "__main__":
    root = tk.Tk()
    app = LEDControlGUI(root)
    root.mainloop()
