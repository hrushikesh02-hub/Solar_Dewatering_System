from flask import Flask, jsonify, request
from flask_cors import CORS
import serial
import serial.tools.list_ports
import threading
import time
import math

app = Flask(__name__)
CORS(app)  # Allow frontend to access Flask

# ==============================
# 🔍 AUTO DETECT ARDUINO (CH340)
# ==============================
def find_arduino():
    ports = serial.tools.list_ports.comports()
    for port in ports:
        print(f"Found port: {port.device} - {port.description}")
        if "CH340" in port.description or "Arduino" in port.description or "USB Serial" in port.description:
            return port.device
    return None

arduino_port = find_arduino()
ser = None

if arduino_port:
    try:
        ser = serial.Serial(arduino_port, 9600, timeout=1)
        time.sleep(2)  # Wait for Arduino reset
        print(f"✅ Connected to Arduino on {arduino_port}")
    except Exception as e:
        print(f"⚠️ Failed to connect: {e}")
        ser = None
else:
    print("⚠️ Arduino not found, running in simulation mode")

# ==============================
# 📊 GLOBAL STATE
# ==============================
moisture = 50
pump = 0
auto_mode = True  # Track autonomous mode
manual_override = False
lock = threading.Lock()

# Simulation mode variables
sim_moisture = 50
sim_pump = 0
sim_auto_mode = True
last_update = time.time()

# ==============================
# 📡 SERIAL READING THREAD
# ==============================
def read_serial():
    global moisture, pump
    
    while True:
        try:
            if ser and ser.in_waiting:
                line = ser.readline().decode(errors='ignore').strip()
                
                if line:
                    print(f"📡 RAW: {line}")
                    
                    # Expected format: "moisture,pump" e.g., "512,1"
                    parts = line.split(',')
                    if len(parts) == 2:
                        try:
                            raw_m = int(parts[0])
                            p = int(parts[1])
                            
                            # Convert 0-1023 to 0-100%
                            m_percent = int((raw_m / 1023) * 100)
                            m_percent = max(0, min(100, m_percent))
                            
                            with lock:
                                moisture = m_percent
                                pump = p
                            
                            print(f"📊 Moisture: {m_percent}%, Pump: {p}")
                        except ValueError:
                            pass
        except Exception as e:
            print(f"⚠️ Serial Error: {e}")
        
        time.sleep(0.1)

# Start serial thread if Arduino connected
if ser:
    thread = threading.Thread(target=read_serial, daemon=True)
    thread.start()
    print("✅ Serial reader thread started")

# ==============================
# 🌿 SIMULATION MODE (with realistic behavior)
# ==============================
def update_simulation():
    global sim_moisture, sim_pump, sim_auto_mode
    
    # Natural moisture variation (sine wave + randomness)
    t = time.time()
    natural_variation = 5 * math.sin(t * 0.01)  # Slow sine wave
    random_noise = (time.time() * 100) % 7 - 3  # Random -3 to +3
    
    # Pump effect: pumping reduces moisture
    if sim_pump == 1:
        moisture_change = -0.5  # Pump reduces moisture
    else:
        moisture_change = 0.2 + (natural_variation + random_noise) * 0.05  # Slow increase
    
    sim_moisture += moisture_change
    sim_moisture = max(15, min(90, sim_moisture))
    
    # Autonomous logic for simulation
    if sim_auto_mode:
        if sim_moisture > 70 and sim_pump == 0:
            sim_pump = 1
            print(f"🤖 AUTO: Pump ON (moisture: {sim_moisture:.1f}%)")
        elif sim_moisture < 40 and sim_pump == 1:
            sim_pump = 0
            print(f"🤖 AUTO: Pump OFF (moisture: {sim_moisture:.1f}%)")

# ==============================
# 🌐 API ROUTES
# ==============================

@app.route('/data')
def get_data():
    """Get current sensor data"""
    global sim_moisture, sim_pump, sim_auto_mode
    
    if ser:
        # Real Arduino mode
        with lock:
            return jsonify({
                "moisture": moisture,
                "pump": pump,
                "auto_mode": auto_mode
            })
    else:
        # Simulation mode
        update_simulation()
        return jsonify({
            "moisture": int(sim_moisture),
            "pump": sim_pump,
            "auto_mode": sim_auto_mode
        })

@app.route('/on')
def turn_on():
    """Manually turn pump ON"""
    global sim_pump, sim_auto_mode
    
    try:
        if ser:
            # Send command to Arduino
            ser.write(b'ON\n')
            with lock:
                pump = 1
        else:
            # Simulation mode
            if sim_auto_mode:
                return jsonify({"error": "Cannot override: Autonomous mode is ON", "success": False}), 400
            sim_pump = 1
        
        return jsonify({"status": "ON", "success": True})
    except Exception as e:
        return jsonify({"error": str(e), "success": False}), 500

@app.route('/off')
def turn_off():
    """Manually turn pump OFF"""
    global sim_pump, sim_auto_mode
    
    try:
        if ser:
            ser.write(b'OFF\n')
            with lock:
                pump = 0
        else:
            # Simulation mode
            if sim_auto_mode:
                return jsonify({"error": "Cannot override: Autonomous mode is ON", "success": False}), 400
            sim_pump = 0
        
        return jsonify({"status": "OFF", "success": True})
    except Exception as e:
        return jsonify({"error": str(e), "success": False}), 500

@app.route('/toggle', methods=['POST'])
def toggle_pump():
    """Toggle pump state (for frontend compatibility)"""
    global sim_pump, sim_auto_mode
    
    try:
        if ser:
            with lock:
                new_state = 1 if pump == 0 else 0
                if new_state == 1:
                    ser.write(b'ON\n')
                else:
                    ser.write(b'OFF\n')
                pump = new_state
        else:
            # Simulation mode
            if sim_auto_mode:
                return jsonify({"error": "Cannot toggle: Autonomous mode is ON", "success": False}), 400
            sim_pump = 1 if sim_pump == 0 else 0
        
        return jsonify({"status": "TOGGLED", "pump": sim_pump if not ser else pump, "success": True})
    except Exception as e:
        return jsonify({"error": str(e), "success": False}), 500

@app.route('/auto-mode', methods=['POST'])
def set_auto_mode():
    """Enable/disable autonomous mode"""
    global auto_mode, sim_auto_mode
    
    data = request.get_json()
    enabled = data.get('enabled', True)
    
    if ser:
        with lock:
            auto_mode = enabled
    else:
        sim_auto_mode = enabled
    
    return jsonify({
        "auto_mode": enabled,
        "success": True,
        "message": f"Autonomous mode {'ON' if enabled else 'OFF'}"
    })

@app.route('/auto-mode', methods=['GET'])
def get_auto_mode():
    """Get current autonomous mode status"""
    if ser:
        with lock:
            return jsonify({"auto_mode": auto_mode})
    else:
        return jsonify({"auto_mode": sim_auto_mode})

@app.route('/status')
def full_status():
    """Get complete system status"""
    if ser:
        with lock:
            return jsonify({
                "moisture": moisture,
                "pump": pump,
                "auto_mode": auto_mode,
                "connected": True,
                "mode": "arduino"
            })
    else:
        update_simulation()
        return jsonify({
            "moisture": int(sim_moisture),
            "pump": sim_pump,
            "auto_mode": sim_auto_mode,
            "connected": False,
            "mode": "simulation"
        })

# ==============================
# ▶️ RUN SERVER
# ==============================
if __name__ == '__main__':
    print("\n" + "="*50)
    print("🚀 Solar Dewatering System Backend")
    print("="*50)
    if ser:
        print(f"✅ Arduino connected on {arduino_port}")
    else:
        print("⚠️ Running in SIMULATION mode")
    print("📡 API available at: http://127.0.0.1:5000")
    print("📊 Endpoints:")
    print("   GET  /data       - Get moisture and pump status")
    print("   GET  /status     - Full system status")
    print("   GET  /on         - Turn pump ON (manual)")
    print("   GET  /off        - Turn pump OFF (manual)")
    print("   POST /toggle     - Toggle pump state")
    print("   GET  /auto-mode  - Get autonomous mode")
    print("   POST /auto-mode  - Set autonomous mode")
    print("="*50 + "\n")
    
    app.run(debug=True, use_reloader=False, host='0.0.0.0', port=5000)