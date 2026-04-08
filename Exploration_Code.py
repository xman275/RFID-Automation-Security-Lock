import RPi.GPIO as GPIO
import time
import smbus
import pigpio

# GPIO Pins configuration (BCM mode)
RELAY_PIN = 21            # Relay/MOSFET controlling power to RFID reader
RX_PIN = 20               # Serial data pin for RFID reader
RGB_LED_RED = 13          # Red pin for RGB LED
RGB_LED_GREEN = 19        # Green pin for RGB LED
RGB_LED_BLUE = 26         # Blue pin for RGB LED
SERVO_PIN = 18            # Servo motor control pin

# IR Range Values
LOWER_IR_VALUE = 140
HIGHER_IR_VALUE = 190

# I²C Configuration
I2C_ADDRESS = 0x48        # I²C address of the ADC (IR sensor connected)
I2C_CHANNEL = 0x42        # Input channel for the IR sensor
bus = smbus.SMBus(1)

# RFID configuration
BAUD_RATE = 2400          # RFID reader baud rate
VALID_TAGS = []           # List of valid tag IDs
ARMED = False             # Armed state flag
last_read_tag = None      # Last RFID tag read

# Initialize RPi.GPIO
GPIO.setwarnings(False)  # Suppress GPIO warnings
GPIO.setmode(GPIO.BCM)
GPIO.setup(RGB_LED_RED, GPIO.OUT)
GPIO.setup(RGB_LED_GREEN, GPIO.OUT)
GPIO.setup(RGB_LED_BLUE, GPIO.OUT)
GPIO.setup(SERVO_PIN, GPIO.OUT)
GPIO.setup(RELAY_PIN, GPIO.OUT)

#Initialize pigpio
pi = pigpio.pi()
if not pi.connected:
    print("Failed to connect to pigpio daemon.")
    exit()

# Initialize PWM for servo motor
servo = GPIO.PWM(SERVO_PIN, 50)  # 50Hz PWM frequency for servo
servo.start(0)

# Initialize PWM for RGB LED
red_pwm = GPIO.PWM(RGB_LED_RED, 100)  # 100Hz frequency
green_pwm = GPIO.PWM(RGB_LED_GREEN, 100)  # 100Hz frequency
blue_pwm = GPIO.PWM(RGB_LED_BLUE, 100)  # 100Hz frequency

# Start PWM with 0% duty cycle (LED off)
red_pwm.start(0)
green_pwm.start(0)
blue_pwm.start(0)

# Initialize RFID relay to off (HIGH if active LOW)
GPIO.output(RELAY_PIN, GPIO.HIGH)

# Maintain a global flag to track bit-bang status
rfid_serial_open = False  # Set to True when opening the serial in setup_rfid()

# Enable bit-banging serial reading on GPIO 20
def setup_rfid():
    """Set up the RFID reader."""
    global rfid_serial_open
    GPIO.output(RELAY_PIN, GPIO.LOW)  # Power on the RFID reader (LOW to activate)
    time.sleep(1)  # Wait for the reader to initialize
    if pi.bb_serial_read_open(RX_PIN, BAUD_RATE, 8) != 0:
        print("ERROR: Failed to open bit-bang serial read on GPIO 20.")
        cleanup_rfid()
        exit()
    rfid_serial_open = True  # Set flag to indicate serial is open

def cleanup_rfid():
    """Clean up the RFID reader."""
    global rfid_serial_open
    try:
        if rfid_serial_open:  # Only close if it was opened
            pi.bb_serial_read_close(RX_PIN)
            rfid_serial_open = False  # Reset the flag
    except pigpio.error as e:
        print(f"WARNING: Error during RFID cleanup: {e}")
    
    GPIO.output(RELAY_PIN, GPIO.HIGH)  # Power off the RFID reader

# Functions
def set_rgb_led_pwm(red_dc=0, green_dc=0, blue_dc=0):
    """
    Set RGB LED color using PWM duty cycle.
    Args:
        red_dc: Duty cycle for the red LED (0-100).
        green_dc: Duty cycle for the green LED (0-100).
        blue_dc: Duty cycle for the blue LED (0-100).
    """
    red_pwm.ChangeDutyCycle(red_dc)
    green_pwm.ChangeDutyCycle(green_dc)
    blue_pwm.ChangeDutyCycle(blue_dc)
    
def flash_led_pwm(color, flashes, interval=0.2, brightness=100):
    """
    Flash RGB LED in specified color using PWM.
    Args:
        color (tuple): (red, green, blue) values where 1=on, 0=off.
        flashes (int): Number of flashes.
        interval (float): Interval between flashes in seconds.
        brightness (int): Brightness level (0 to 100).
    """
    for _ in range(flashes):
        set_rgb_led_pwm(*(brightness * c for c in color))  # Scale brightness
        time.sleep(interval)
        set_rgb_led_pwm(0, 0, 0)  # Turn off
        time.sleep(interval)

def rgb_led_off(red, green, blue):
    red.stop(0)
    green.stop(0)
    blue.stop(0)

def unlock_servo():
    """Unlock the servo (90 degrees)."""
    servo.ChangeDutyCycle(7.5)
    set_rgb_led_pwm(0, 70, 0)
    system_locked = False
    time.sleep(0.5)

def lock_servo():
    """Lock the servo (0 degrees)."""
    servo.ChangeDutyCycle(2.5)
    set_rgb_led_pwm(70, 0, 0)
    system_locked = True
    time.sleep(0.5)

# Function to read the infrared sensor value via I²C
def read_ir_sensor():
    """Read the infrared sensor value using I²C."""
    bus.write_byte(I2C_ADDRESS, I2C_CHANNEL)
    value = bus.read_byte(I2C_ADDRESS)
    return value

def read_tag_once(pi, rx_pin, timeout=2):
    """
    Reads a complete RFID tag value from the specified GPIO pin, ensuring it has 10 characters.
    
    Args:
        pi: The pigpio.pi() instance.
        rx_pin: The GPIO pin connected to the RFID reader's RX.
        timeout: Time in seconds to wait for a complete tag.
        
    Returns:
        The extracted tag value as a string, or None if timeout occurs or tag is invalid.
    """
    end_time = time.time() + timeout
    tag_data = b''

    while time.time() < end_time:
        count, data = pi.bb_serial_read(rx_pin)
        if count > 0:
            tag_data += data

            # Look for \r\n as delimiters
            if b'\r\n' in tag_data:
                split_data = tag_data.split(b'\r\n')
                
                # Extract the tag data between \r\n
                if len(split_data) > 1:
                    clean_tag = split_data[1].decode('utf-8').strip()  # Extract second segment
                    tag_data = b'\r\n'.join(split_data[2:])  # Keep leftover data
                
                    # Validate tag length
                    if len(clean_tag) == 10:
                        return clean_tag
                
        time.sleep(0.1)  # Small delay to prevent CPU overuse
    return None
    
def read_rfid_tags(pi, rx_pin, interval=2):
    """
    Reads RFID tags at specified intervals.
    
    Args:
        pi: The pigpio.pi() instance.
        rx_pin: The GPIO pin connected to the RFID reader's RX.
        interval: Time in seconds between reads.
        
    Yields:
        The tag value as a string.
    """
    while True:
        tag = read_tag_once(pi, rx_pin)
        if tag:
            yield tag
        time.sleep(interval)
    
def validate_rfid_tag():
    """
    Validate the RFID tag read by the reader against VALID_TAGS.
    Returns:
        None
    """
    tag = read_tag_once(pi, RX_PIN, timeout=1)  # Read a single tag once
    if tag:
        if tag in VALID_TAGS:
            print(f"Valid tag detected: {tag}. Unlocking the servo for 5 secs.")
            unlock_servo()  # Unlock the servo
            time.sleep(5)  # Keep unlocked for 5 seconds
            lock_servo()  # Lock the servo back
        else:
            print(f"Invalid tag detected: {tag}. Flashing red LED.")
            flash_led_pwm((1, 0, 0), 3, 0.3)  # Flash red LED 3 times
        
def monitor_armed_mode():
    """Monitor IR sensor and activate/deactivate RFID reader in armed mode."""
    global ARMED

    relay_state = False  # Initial state of the relay (inactive/HIGH)

    while ARMED:  # Continue monitoring while the system is armed
        ir_value = read_ir_sensor()  # Read the IR sensor value via I²C

        if LOWER_IR_VALUE <= ir_value <= HIGHER_IR_VALUE:
            if not relay_state:  # Only activate if not already active
                GPIO.output(RELAY_PIN, GPIO.LOW)  # Power on the RFID reader (LOW to activate)
                relay_state = True
                
            validate_rfid_tag()
        else:
            if relay_state:  # Only deactivate if not already inactive
                GPIO.output(RELAY_PIN, GPIO.HIGH)  # Power off the RFID reader (HIGH to deactivate)
                relay_state = False

        time.sleep(0.5)  # Small delay to avoid CPU overload

# Main program loop
try:
    while True:
        if ARMED:
            set_rgb_led_pwm(70, 0, 0)
        else:
            set_rgb_led_pwm(0, 40, 40)
        print("\nMain Menu:")
        print("1. Arm System")
        print("2. Disarm System")
        print("3. Program Mode")
        print("4. Exit")
        choice = input("Select an option: ").strip()

        if choice == "1":
            # Armed Mode
            ARMED = True
            setup_rfid()
            lock_servo()  # Ensure system starts locked
            print("System is now armed.")
            
            try:
                monitor_armed_mode()  # Start monitoring IR sensor in armed mode
            except KeyboardInterrupt:
                print("Armed mode interrupted by user.")
                ARMED = False  # Exit armed mode if interrupted
                cleanup_rfid()
                
        elif choice == "2":
            # Disarmed Mode
            ARMED = False
            unlock_servo()
            print("System is now disarmed.")
        elif choice == "3":
            set_rgb_led_pwm(0, 0, 70) # Turn Blue LED on
            # Program Mode
            print("\nProgram Mode:")
            print("1. Load Tags")
            print("2. Unload Tags")
            print("3. View Tags")

            program_choice = input("Select an option: ").strip()
            if program_choice == "1":
                # Load Tags
                setup_rfid()  # Power on RFID reader
                print("Load Tags Mode. Scan RFID tags to add. Type 'done' to finish.")
                tag_reader = read_rfid_tags(pi, RX_PIN, interval=2)
                while True:
                    try:
                        tag_id = next(tag_reader)  # Get the next unique tag from the generator
                        if tag_id not in VALID_TAGS:
                            VALID_TAGS.append(tag_id)
                            print(f"Tag added: {tag_id}")
                        else:
                            print(f"Tag already exists: {tag_id}")
                    except StopIteration:
                        print("RFID reading interrupted.")

                    user_input = input("Continue adding tags? - press Enter (Type 'done' to finish): ").strip().lower()
                    if user_input == "done":
                        break
                cleanup_rfid()
            elif program_choice == "2":
                # Unload Tags
                print("Unload Tags Mode. Type tag ID to remove or 'clear' to remove all. (Type 'done' to finish)")
                while True:
                    print(f"Current tags: {VALID_TAGS}")
                    tag_to_remove = input("Enter tag to remove or 'clear' to remove all: ").strip()
                    if tag_to_remove.lower() == "clear":
                        VALID_TAGS.clear()
                        print("All tags removed.")
                        break
                    elif tag_to_remove in VALID_TAGS:
                        VALID_TAGS.remove(tag_to_remove)
                        print(f"Tag removed: {tag_to_remove}")
                    elif tag_to_remove == "done":
                        break
                    else:
                        print("Tag not found.")
            elif program_choice == "3":
                # View Tags
                print(f"\nCurrent Tags: {VALID_TAGS}")
        elif choice == "4":
            # Exit
            print("\nExiting system.")
            break
        else:
            print("Invalid choice. Please try again.")

except KeyboardInterrupt:
    print("\nProgram interrupted by user.")

finally:
    # Clean up PWM for RGB LED
    red_pwm.stop()
    green_pwm.stop()
    blue_pwm.stop()
    
    # Clean up servo PWM
    servo.stop()

    # Clean up resources
    cleanup_rfid()
    pi.stop()
    GPIO.cleanup()
    print("System cleaned up and exited.")
