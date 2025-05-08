from flask import Flask, Response, render_template, request, jsonify
from picamera2 import Picamera2
import io
import time
import cv2
import numpy as np
import pigpio
import threading

app = Flask(__name__)

# Initialize the camera
picam2 = Picamera2()
picam2.configure(picam2.create_video_configuration(main={"size": (640, 480)}))
picam2.start()

# Initialize pigpio
pi = pigpio.pi()

# Define the GPIO pins
SERVO_PAN_PIN = 18    # Horizontal movement (left/right)
SERVO_TILT_PIN = 19   # Vertical movement (up/down)
MOTOR_PIN_1 = 12      # Motor control pin 1
MOTOR_PIN_2 = 13      # Motor control pin 2 (if using a dual motor setup)

# PWM frequency for motor control
MOTOR_PWM_FREQ = 1000  # 1 kHz

# Check if pigpio daemon is running
if not pi.connected:
    print("Error: Could not connect to pigpio daemon. Is it running?")
    print("Try running 'sudo pigpiod' first.")
    exit()

# Set up servo initial positions (center)
pi.set_servo_pulsewidth(SERVO_PAN_PIN, 1500)  # Center position
pi.set_servo_pulsewidth(SERVO_TILT_PIN, 1500) # Center position

# Set up motor pins as output and initialize to off
pi.set_mode(MOTOR_PIN_1, pigpio.OUTPUT)
pi.set_mode(MOTOR_PIN_2, pigpio.OUTPUT)

# Set PWM frequency
pi.set_PWM_frequency(MOTOR_PIN_1, MOTOR_PWM_FREQ)
pi.set_PWM_frequency(MOTOR_PIN_2, MOTOR_PWM_FREQ)

# Initialize motor PWM to 0 (off)
pi.set_PWM_dutycycle(MOTOR_PIN_1, 0)
pi.set_PWM_dutycycle(MOTOR_PIN_2, 0)

# Current servo positions
servo_positions = {
    'pan': 1500,   # Range 500-2500 (0 = center)
    'tilt': 1500   # Range 500-2500 (0 = center)
}

# Current motor power
motor_power = 0    # Range 0-100%

def generate_frames():
    """Video streaming generator function."""
    while True:
        frame = picam2.capture_array()
        _, buffer = cv2.imencode('.jpg', frame)
        frame_bytes = buffer.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

# Create HTML template with servo control arrows and motor power slider
@app.route('/')
def index():
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Dog Ball Launcher Control</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {
                font-family: Arial, sans-serif;
                max-width: 900px;
                margin: 0 auto;
                padding: 20px;
                background-color: #f5f5f5;
            }
            h1 {
                color: #333;
                text-align: center;
            }
            .video-container {
                margin-bottom: 20px;
                text-align: center;
            }
            .video-container img {
                max-width: 100%;
                border-radius: 8px;
                box-shadow: 0 4px 8px rgba(0, 0, 0, 0.1);
            }
            .controls {
                display: flex;
                flex-direction: column;
                gap: 20px;
                background-color: white;
                padding: 20px;
                border-radius: 8px;
                box-shadow: 0 4px 8px rgba(0, 0, 0, 0.1);
            }
            .control-group {
                display: flex;
                flex-direction: column;
                align-items: center;
                gap: 10px;
            }
            .control-row {
                display: flex;
                gap: 10px;
                justify-content: center;
            }
            .arrow-btn {
                width: 60px;
                height: 60px;
                font-size: 24px;
                border: none;
                background-color: #4CAF50;
                color: white;
                border-radius: 8px;
                cursor: pointer;
                display: flex;
                align-items: center;
                justify-content: center;
            }
            .arrow-btn:hover {
                background-color: #45a049;
            }
            .center-btn {
                background-color: #2196F3;
            }
            .center-btn:hover {
                background-color: #0b7dda;
            }
            .slider-container {
                width: 100%;
                max-width: 500px;
                margin: 0 auto;
            }
            .slider-container label {
                display: block;
                margin-bottom: 10px;
                font-weight: bold;
            }
            #motor-power {
                width: 100%;
            }
            .power-value {
                text-align: center;
                font-weight: bold;
                margin-top: 5px;
            }
            .launch-btn {
                padding: 15px 30px;
                font-size: 18px;
                background-color: #FF5722;
                color: white;
                border: none;
                border-radius: 8px;
                cursor: pointer;
                margin: 20px auto;
                display: block;
            }
            .launch-btn:hover {
                background-color: #E64A19;
            }
            .status {
                text-align: center;
                margin-top: 10px;
                font-style: italic;
                color: #666;
            }
        </style>
    </head>
    <body>
        <h1>Dog Ball Launcher Control</h1>
        <div class="video-container">
            <img src="/video_feed" alt="Camera Feed">
        </div>
        <div class="controls">
            <div class="control-group">
                <h2>Direction Control</h2>
                <div class="control-row">
                    <div></div>
                    <button class="arrow-btn" onclick="moveServo('up')">↑</button>
                    <div></div>
                </div>
                <div class="control-row">
                    <button class="arrow-btn" onclick="moveServo('left')">←</button>
                    <button class="arrow-btn center-btn" onclick="moveServo('center')">◎</button>
                    <button class="arrow-btn" onclick="moveServo('right')">→</button>
                </div>
                <div class="control-row">
                    <div></div>
                    <button class="arrow-btn" onclick="moveServo('down')">↓</button>
                    <div></div>
                </div>
            </div>
            
            <div class="control-group">
                <h2>Motor Power</h2>
                <div class="slider-container">
                    <label for="motor-power">Power: <span id="power-display">0%</span></label>
                    <input type="range" id="motor-power" name="motor-power" min="0" max="100" value="0" oninput="updatePower(this.value)">
                </div>
            </div>
            
            <button class="launch-btn" onclick="launchBall()">LAUNCH BALL</button>
            <div class="status" id="status"></div>
        </div>

        <script>
            // Update displayed power value and send to server
            function updatePower(value) {
                document.getElementById('power-display').textContent = value + '%';
                fetch('/motor?power=' + value)
                    .then(response => response.text())
                    .then(data => {
                        console.log(data);
                    });
            }
            
            // Move servo in specific direction
            function moveServo(direction) {
                fetch('/servo?direction=' + direction)
                    .then(response => response.text())
                    .then(data => {
                        console.log(data);
                        document.getElementById('status').textContent = 'Moved: ' + direction;
                    });
            }
            
            // Launch the ball
            function launchBall() {
                document.getElementById('status').textContent = 'Launching ball...';
                fetch('/launch')
                    .then(response => response.text())
                    .then(data => {
                        console.log(data);
                        document.getElementById('status').textContent = data;
                    });
            }
            
            // Add keyboard support for arrow keys
            document.addEventListener('keydown', function(event) {
                switch(event.key) {
                    case 'ArrowUp':
                        moveServo('up');
                        break;
                    case 'ArrowDown':
                        moveServo('down');
                        break;
                    case 'ArrowLeft':
                        moveServo('left');
                        break;
                    case 'ArrowRight':
                        moveServo('right');
                        break;
                    case ' ':  // Spacebar
                        launchBall();
                        break;
                }
            });
        </script>
    </body>
    </html>
    '''

@app.route('/video_feed')
def video_feed():
    """Video streaming route."""
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/servo')
def control_servo():
    global servo_positions
    direction = request.args.get('direction', 'center')
    
    # Servo step size (microseconds)
    step = 100
    
    # Update servo positions based on direction
    if direction == 'left':
        servo_positions['pan'] = max(500, servo_positions['pan'] - step)
    elif direction == 'right':
        servo_positions['pan'] = min(2500, servo_positions['pan'] + step)
    elif direction == 'up':
        servo_positions['tilt'] = max(500, servo_positions['tilt'] - step)
    elif direction == 'down':
        servo_positions['tilt'] = min(2500, servo_positions['tilt'] + step)
    elif direction == 'center':
        servo_positions['pan'] = 1500
        servo_positions['tilt'] = 1500
    
    # Apply the new servo positions
    pi.set_servo_pulsewidth(SERVO_PAN_PIN, servo_positions['pan'])
    pi.set_servo_pulsewidth(SERVO_TILT_PIN, servo_positions['tilt'])
    
    return f"Servo moved: {direction}, Pan: {servo_positions['pan']}, Tilt: {servo_positions['tilt']}"

@app.route('/motor')
def control_motor():
    global motor_power
    power = int(request.args.get('power', 0))
    
    # Validate power value (0-100%)
    power = max(0, min(100, power))
    motor_power = power
    
    # Convert percentage to PWM value (0-255)
    pwm_value = int(power * 255 / 100)
    
    # Apply PWM to motor
    pi.set_PWM_dutycycle(MOTOR_PIN_1, pwm_value)
    
    # For bidirectional control, you might do something like:
    # if power > 0:
    #     pi.set_PWM_dutycycle(MOTOR_PIN_1, pwm_value)
    #     pi.set_PWM_dutycycle(MOTOR_PIN_2, 0)
    # else:
    #     pi.set_PWM_dutycycle(MOTOR_PIN_1, 0)
    #     pi.set_PWM_dutycycle(MOTOR_PIN_2, abs(pwm_value))
    
    return f"Motor power set to {power}%"

@app.route('/launch')
def launch_ball():
    # This function could activate a servo to release a ball
    # For this example, we'll simulate a ball release
    try:
        # Get current power level
        power = motor_power
        
        if power < 10:
            return "Motor power too low. Increase power to launch."
        
        # Example: Use a servo to release a ball
        # Assuming a third servo on pin 20 controls the ball release mechanism
        RELEASE_SERVO_PIN = 20
        
        # Open the release mechanism
        pi.set_servo_pulsewidth(RELEASE_SERVO_PIN, 2000)  # Position to release ball
        time.sleep(0.5)  # Wait for ball to release
        
        # Close the release mechanism
        pi.set_servo_pulsewidth(RELEASE_SERVO_PIN, 1000)  # Position to hold balls
        
        return f"Ball launched with power: {power}%"
        
    except Exception as e:
        return f"Launch error: {str(e)}"

# Cleanup function to ensure all resources are properly released
def cleanup():
    # Stop all servos
    pi.set_servo_pulsewidth(SERVO_PAN_PIN, 0)
    pi.set_servo_pulsewidth(SERVO_TILT_PIN, 0)
    
    # Stop motors
    pi.set_PWM_dutycycle(MOTOR_PIN_1, 0)
    pi.set_PWM_dutycycle(MOTOR_PIN_2, 0)
    
    # Stop pigpio connection
    pi.stop()
    print("Cleaned up GPIO resources")

# Register cleanup function to run when app shuts down
import atexit
atexit.register(cleanup)

if __name__ == '__main__':
    # "PUSHTEST" comment preserved as requested
    print("Starting Dog Ball Launcher Control Server...")
    try:
        app.run(host='0.0.0.0', port=5000, threaded=True)
    except KeyboardInterrupt:
        print("Server stopped by user")
    finally:
        cleanup()