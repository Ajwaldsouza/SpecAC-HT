#!/usr/bin/env python
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import serial
import threading
import time
import json
from serial.tools import list_ports

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
        
        self.create_gui()
        
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
        
        # Scan and Apply buttons
        btn_frame = ttk.Frame(header_frame)
        btn_frame.pack(side=tk.RIGHT)
        
        ttk.Button(btn_frame, text="Scan for Boards", command=self.scan_boards).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Apply All Settings", command=self.apply_all_settings).pack(side=tk.LEFT, padx=5)
        
        # Add Import/Export buttons
        ttk.Button(btn_frame, text="Export Settings", command=self.export_settings).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Import Settings", command=self.import_settings).pack(side=tk.LEFT, padx=5)
        
        # Status bar
        self.status_var = tk.StringVar(value="Ready")
        status_bar = ttk.Label(main_frame, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.grid(column=0, row=2, columnspan=2, sticky=(tk.W, tk.E))
        
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
        
        self.scrollable_frame = scrollable_frame
        self.scan_boards()
        
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
            
            # Add LED controls for each channel
            for row, (channel_name, channel_idx) in enumerate(LED_CHANNELS.items()):
                color_frame = ttk.Frame(frame, width=20, height=20)
                color_frame.grid(column=0, row=row, padx=5, pady=2)
                color_label = tk.Label(color_frame, bg=LED_COLORS[channel_name], width=2)
                color_label.pack(fill=tk.BOTH, expand=True)
                
                ttk.Label(frame, text=channel_name).grid(column=1, row=row, sticky=tk.W, padx=5)
                
                value_var = tk.StringVar(value="0")
                entry = ttk.Spinbox(
                    frame, 
                    from_=0, 
                    to=100, 
                    width=5, 
                    textvariable=value_var,
                    validate='key',
                    validatecommand=(self.root.register(self.validate_percentage), '%P')
                )
                entry.grid(column=2, row=row, sticky=tk.W, padx=5)
                ttk.Label(frame, text="%").grid(column=3, row=row, sticky=tk.W)
                
                self.led_entries[(i, channel_name)] = entry
                
            # Individual apply button
            ttk.Button(
                frame, 
                text="Apply", 
                command=lambda b_idx=i: self.apply_board_settings(b_idx)
            ).grid(column=1, row=len(LED_CHANNELS), columnspan=3, pady=10)
    
    def validate_percentage(self, value):
        """Validate that entry is a valid percentage (0-100)"""
        if value == "":
            return True
        try:
            val = int(value)
            return 0 <= val <= 100
        except ValueError:
            return False
    
    def apply_board_settings(self, board_idx):
        """Apply settings for a specific board"""
        if board_idx >= len(self.boards):
            messagebox.showerror("Error", "Invalid board index")
            return
            
        board = self.boards[board_idx]
        duty_values = []
        
        # Get duty cycle values for each channel
        for channel in LED_CHANNELS:
            try:
                percentage = int(self.led_entries[(board_idx, channel)].get())
                duty = int((percentage / 100.0) * 4095)
                duty_values.append(duty)
            except ValueError:
                duty_values.append(0)
        
        # Send command to the board
        success, message = board.send_command(duty_values)
        
        if success:
            self.status_var.set(f"Board {board_idx+1}: Settings applied successfully")
        else:
            messagebox.showerror(f"Error - Board {board_idx+1}", message)
            self.status_var.set(f"Board {board_idx+1}: Error - {message}")
    
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
        """Export current LED settings to a text file"""
        if not self.boards:
            messagebox.showwarning("No Boards", "No boards available to export settings from.")
            return
            
        try:
            # Collect all settings
            settings = {}
            for board_idx in range(len(self.boards)):
                board_settings = {}
                for channel_name in LED_CHANNELS:
                    try:
                        value = int(self.led_entries[(board_idx, channel_name)].get())
                        board_settings[channel_name] = value
                    except (ValueError, KeyError):
                        board_settings[channel_name] = 0
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
        """Import LED settings from a text file and apply them"""
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
                        
                    for channel_name, value in board_settings.items():
                        if channel_name in LED_CHANNELS and (board_idx, channel_name) in self.led_entries:
                            self.led_entries[(board_idx, channel_name)].delete(0, tk.END)
                            self.led_entries[(board_idx, channel_name)].insert(0, str(value))
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
