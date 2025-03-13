import tkinter as tk
from tkinter import messagebox
import serial
import serial.tools.list_ports

def main():
    # Detect connected XIAO RP2040 boards
    ports = list(serial.tools.list_ports.grep('VID:PID=2E8A:0005'))
    boards = []
    
    # Establish serial connections for up to 16 boards
    for port in ports[:16]:
        try:
            ser = serial.Serial(port.device, 115200, timeout=1)
            boards.append({'serial_number': port.serial_number, 'ser': ser})
        except serial.SerialException as e:
            print(f"Failed to open {port.device}: {e}")
            messagebox.showerror("Error", f"Serial connection failed for {port.device}: {e}")
    
    # Check the number of connected boards and show a warning if not exactly 16
    num_boards = len(boards)
    if num_boards == 0:
        messagebox.showerror("Error", "No XIAO RP2040 boards detected.")
        return
    elif num_boards < 16:
        messagebox.showwarning("Warning", f"Only {num_boards} board(s) detected out of 16.")

    # Initialize the Tkinter GUI
    root = tk.Tk()
    root.title("LED Brightness Control")
    
    # Create a frame for the canvas and scrollbar
    canvas_frame = tk.Frame(root)
    canvas_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
    
    # Create a scrollable canvas for the board columns
    canvas = tk.Canvas(canvas_frame)
    canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
    
    scrollbar = tk.Scrollbar(canvas_frame, orient=tk.HORIZONTAL, command=canvas.xview)
    scrollbar.pack(side=tk.BOTTOM, fill=tk.X)
    canvas.configure(xscrollcommand=scrollbar.set)
    
    # Frame to hold all board columns
    boards_frame = tk.Frame(canvas)
    canvas.create_window((0, 0), window=boards_frame, anchor='nw')
    
    # Update scroll region when the frame size changes
    boards_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    
    # Dictionary to store entry widgets for each board
    entries = {}
    channel_names = ['UV', 'FAR_RED', 'RED', 'WHITE', 'GREEN', 'BLUE']
    
    # Create GUI elements for each detected board
    for i, board in enumerate(boards):
        serial_number = board['serial_number']
        
        # Use LabelFrame with serial number as the title
        board_frame = tk.LabelFrame(boards_frame, text=serial_number)
        board_frame.grid(row=0, column=i, padx=10, pady=10)
        
        board_entries = []
        # Add labels and entry fields for each LED channel
        for channel in channel_names:
            frame = tk.Frame(board_frame)
            frame.pack()
            tk.Label(frame, text=channel).pack(side=tk.LEFT)
            entry = tk.Entry(frame, width=5)
            entry.pack(side=tk.LEFT)
            board_entries.append(entry)
        
        entries[serial_number] = board_entries
    
    # Function to apply settings to all boards
    def apply_settings():
        for board in boards:
            serial_number = board['serial_number']
            ser = board['ser']
            board_entries = entries[serial_number]
            
            try:
                percentages = []
                # Validate and collect input for each channel
                for channel, entry in zip(channel_names, board_entries):
                    value = entry.get().strip()
                    if not value:  # Allow empty fields as 0%
                        p = 0.0
                    else:
                        p = float(value)
                        if not 0 <= p <= 100:
                            raise ValueError(f"Value {value} for {channel} is out of range (0-100).")
                    percentages.append(p)
                
                # Convert percentages to PWM duty cycles (0-4095)
                duties = [int((p / 100.0) * 4095) for p in percentages]
                command = "SETALL " + " ".join(map(str, duties)) + "\n"
                ser.write(command.encode())
            
            except ValueError as e:
                messagebox.showerror("Error", f"Input error for board {serial_number}: {e}")
            except serial.SerialException as e:
                messagebox.showerror("Error", f"Serial error for board {serial_number}: {e}")
    
    # Add the Apply button
    apply_button = tk.Button(root, text="Apply", command=apply_settings)
    apply_button.pack(side=tk.BOTTOM, fill=tk.X)
    
    # Start the GUI event loop
    root.mainloop()
    
    # Clean up serial connections on exit
    for board in boards:
        if board['ser'].is_open:
            board['ser'].close()

if __name__ == "__main__":
    main()