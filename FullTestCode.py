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
SERVO_DIRECTION_PIN = 18  # Left/right movement servo
SERVO_RELEASE_PIN = 19    # Ball release ("afvoer") servo
MOTOR_PIN_1 = 12          # Motor control pin 1
MOTOR_PIN_2 = 13          # Motor control pin 2 (for the second motor)

# PWM frequency for motor control
MOTOR_PWM_FREQ = 1000  # 1 kHz

# Check if pigpio daemon is running
if not pi.connected:
    print("Error: Could not connect to pigpio daemon. Is it running?")
    print("Try running 'sudo pigpiod' first.")
    exit()

# Set up servo initial positions
pi.set_servo_pulsewidth(SERVO_DIRECTION_PIN, 1500)  # Center position
pi.set_servo_pulsewidth(SERVO_RELEASE_PIN, 1000)    # Closed position (holding balls)

# Set up motor pins as output and initialize to off
pi.set_mode(MOTOR_PIN_1, pigpio.OUTPUT)
pi.set_mode(MOTOR_PIN_2, pigpio.OUTPUT)

# Set PWM frequency
pi.set_PWM_frequency(MOTOR_PIN_1, MOTOR_PWM_FREQ)
pi.set_PWM_frequency(MOTOR_PIN_2, MOTOR_PWM_FREQ)

# Initialize motor PWM to 0 (off)
pi.set_PWM_dutycycle(MOTOR_PIN_1, 0)
pi.set_PWM_dutycycle(MOTOR_PIN_2, 0)

# Current servo position for direction
direction_position = 1500  # Range 500-2500 (1500 = center)

# Current motor power
motor_power = 0    # Range 0-100%

# Launch lock to prevent multiple launches at once
launch_lock = threading.Lock()
is_launching = False

def generate_frames():
    """Video streaming generator function."""
    while True:
        frame = picam2.capture_array()
        _, buffer = cv2.imencode('.jpg', frame)
        frame_bytes = buffer.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

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
            .direction-btn {
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
            .direction-btn:hover {
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
                width: 200px;
            }
            .launch-btn:hover {
                background-color: #E64A19;
            }
            .launch-btn:disabled {
                background-color: #cccccc;
                cursor: not-allowed;
            }
            .status {
                text-align: center;
                margin-top: 10px;
                font-style: italic;
                color: #666;
                min-height: 20px;
            }
            .countdown {
                font-size: 18px;
                font-weight: bold;
                color: #FF5722;
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
                    <button class="direction-btn" onclick="moveDirection('left')">←</button>
                    <button class="direction-btn center-btn" onclick="moveDirection('center')">◎</button>
                    <button class="direction-btn" onclick="moveDirection('right')">→</button>
                </div>
            </div>
            
            <div class="control-group">
                <h2>Motor Power</h2>
                <div class="slider-container">
                    <label for="motor-power">Power: <span id="power-display">0%</span></label>
                    <input type="range" id="motor-power" name="motor-power" min="0" max="100" value="0" oninput="updatePower(this.value)">
                </div>
            </div>
            
            <button id="launch-btn" class="launch-btn" onclick="launchBall()">LAUNCH BALL</button>
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
            
            // Move direction servo
            function moveDirection(direction) {
                fetch('/servo?direction=' + direction)
                    .then(response => response.text())
                    .then(data => {
                        console.log(data);
                        document.getElementById('status').textContent = 'Direction: ' + direction;
                    });
            }
            
            // Launch the ball
            function launchBall() {
                // Disable launch button
                const launchBtn = document.getElementById('launch-btn');
                launchBtn.disabled = true;
                
                const statusElement = document.getElementById('status');
                statusElement.innerHTML = 'Launching: Motors starting...';
                
                fetch('/launch')
                    .then(response => response.json())
                    .then(data => {
                        console.log(data);
                        
                        // Start countdown display
                        let secondsLeft = 6; // Total launch sequence time
                        
                        const countdownInterval = setInterval(() => {
                            secondsLeft -= 1;
                            
                            if (secondsLeft <= 3) {
                                statusElement.innerHTML = `Launching: Ball released, motors stopping in ${secondsLeft} seconds...`;
                            } else {
                                statusElement.innerHTML = `Launching: Motors running, ball release in ${secondsLeft - 3} seconds...`;
                            }
                            
                            if (secondsLeft <= 0) {
                                clearInterval(countdownInterval);
                                statusElement.textContent = 'Launch complete!';
                                launchBtn.disabled = false;
                            }
                        }, 1000);
                    })
                    .catch(error => {
                        console.error('Launch error:', error);
                        statusElement.textContent = 'Launch failed! Please try again.';
                        launchBtn.disabled = false;
                    });
            }
            
            // Add keyboard support
            document.addEventListener('keydown', function(event) {
                switch(event.key) {
                    case 'ArrowLeft':
                        moveDirection('left');
                        break;
                    case 'ArrowRight':
                        moveDirection('right');
                        break;
                    case ' ':  // Spacebar
                        if (!document.getElementById('launch-btn').disabled) {
                            launchBall();
                        }
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
    global direction_position
    direction = request.args.get('direction', 'center')
    
    # Servo step size (microseconds)
    step = 100
    
    # Update servo position based on direction
    if direction == 'left':
        direction_position = max(500, direction_position - step)
    elif direction == 'right':
        direction_position = min(2500, direction_position + step)
    elif direction == 'center':
        direction_position = 1500
    
    # Apply the new servo position
    pi.set_servo_pulsewidth(SERVO_DIRECTION_PIN, direction_position)
    
    return f"Direction servo moved: {direction}, Position: {direction_position}"

@app.route('/motor')
def control_motor():
    global motor_power
    power = int(request.args.get('power', 0))
    
    # Validate power value (0-100%)
    power = max(0, min(100, power))
    motor_power = power
    
    # If we're not in a launch sequence, update the motor power
    if not is_launching:
        # Convert percentage to PWM value (0-255)
        pwm_value = int(power * 255 / 100)
        
        # Apply PWM to motors (both motors get the same power)
        pi.set_PWM_dutycycle(MOTOR_PIN_1, pwm_value)
        pi.set_PWM_dutycycle(MOTOR_PIN_2, pwm_value)
    
    return f"Motor power set to {power}%"

@app.route('/launch')
def launch_ball():
    global is_launching
    
    # Prevent multiple launches at once
    if is_launching:
        return jsonify({"status": "error", "message": "Launch already in progress"})
    
    # Get current power level
    power = motor_power
    
    if power < 10:
        return jsonify({"status": "error", "message": "Motor power too low"})
    
    # Start the launch sequence in a separate thread
    threading.Thread(target=launch_sequence, args=(power,)).start()
    
    return jsonify({"status": "success", "message": "Launch sequence started"})

def launch_sequence(power):
    global is_launching
    
    try:
        with launch_lock:
            is_launching = True
            
            # Convert percentage to PWM value (0-255)
            pwm_value = int(power * 255 / 100)
            
            # Step 1: Start motors
            pi.set_PWM_dutycycle(MOTOR_PIN_1, pwm_value)
            pi.set_PWM_dutycycle(MOTOR_PIN_2, pwm_value)
            
            # Step 2: Wait 3 seconds
            time.sleep(3)
            
            # Step 3: Trigger ball release servo
            pi.set_servo_pulsewidth(SERVO_RELEASE_PIN, 2000)  # Open position to release ball
            time.sleep(0.5)  # Give the ball time to drop
            pi.set_servo_pulsewidth(SERVO_RELEASE_PIN, 1000)  # Close position to hold remaining balls
            
            # Step 4: Wait 3 more seconds with motors still running
            time.sleep(3)
            
            # Step 5: Stop motors
            pi.set_PWM_dutycycle(MOTOR_PIN_1, 0)
            pi.set_PWM_dutycycle(MOTOR_PIN_2, 0)
            
    except Exception as e:
        print(f"Launch sequence error: {str(e)}")
    finally:
        is_launching = False

# Cleanup function to ensure all resources are properly released
def cleanup():
    # Stop all servos
    pi.set_servo_pulsewidth(SERVO_DIRECTION_PIN, 0)
    pi.set_servo_pulsewidth(SERVO_RELEASE_PIN, 0)
    
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