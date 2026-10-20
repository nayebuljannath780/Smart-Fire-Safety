from environment import FireEnvironment
from node import SensorNode
from model import FireDetectionModel
import time


# System Initialization
# -------------------------------

print("=" * 50)
print(" IoT Building Fire Safety System ")
print("=" * 50)


# Create environment
environment = FireEnvironment()


# Create sensor node
smoke_heat_sensor = SensorNode("Sensor_001")


# Create fire detection model
fire_model = FireDetectionModel()



# Function: Emergency Response
# -------------------------------

def emergency_response():

    print("\n🚨 EMERGENCY RESPONSE ACTIVATED")
    print("--------------------------------")
    print("🔊 Alarm Siren: ON")
    print("💡 Strobe Light: ON")
    print("📱 Mobile Notification: SENT")
    print("🏢 Building Management System: ACTIVATED")
    print("🌬️ HVAC Control: OFF")
    print("🚪 Fire Door Control: ACTIVE")



# Function: Run Simulation
# -------------------------------

def run_simulation(fire_condition):

    print("\nStarting Sensor Monitoring...")
    time.sleep(1)


    # Generate environmental data
    sensor_data = environment.generate_data(
        fire=fire_condition
    )


    # Sensor collects data
    received_data = smoke_heat_sensor.collect_data(
        sensor_data
    )


    print("\n📡 Sensor Data Received")
    print("--------------------------------")

    print(
        f"Sensor ID: {received_data['node_id']}"
    )

    print(
        f"Temperature: {received_data['temperature']} °C"
    )

    print(
        f"Smoke Level: {received_data['smoke']} %"
    )

    print(
        f"Humidity: {received_data['humidity']} %"
    )



    # Send data to detection model

    prediction = fire_model.predict(
        received_data
    )


    print("\n🧠 Fire Detection Model Result")
    print("--------------------------------")

    print(
        "Prediction:",
        prediction
    )


    # Emergency decision

    if prediction == "FIRE":

        emergency_response()

    else:

        print("\n✅ System Status: Normal Monitoring")



# Main Program
# -------------------------------


if __name__ == "__main__":


    print("\nScenario 1: Normal Environment")
    print("--------------------------------")

    run_simulation(
        fire_condition=False
    )


    time.sleep(2)


    print("\n\nScenario 2: Fire Emergency")
    print("--------------------------------")

    run_simulation(
        fire_condition=True
    )


    print("\n\nSystem Simulation Completed")