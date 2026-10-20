"""Deterministic teaching simulation. No hardware or external network is used."""

import argparse
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter


TRIGGER_RULE = "temperature > 60 C AND smoke > 50%, twice consecutively"
BASE_TIME = datetime(2026, 10, 21, tzinfo=timezone.utc)
ZONES = ("Kitchen", "Office")


@dataclass(frozen=True)
class Reading:
    zone_id: str
    node_id: str
    timestamp: datetime
    temperature: float | None
    smoke: float | None
    humidity: float | None

    def valid(self):
        """Ranges are simulation assumptions, not calibrated sensor limits."""
        values = ((self.temperature, -40, 150), (self.smoke, 0, 100),
                  (self.humidity, 0, 100))
        return all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and low <= value <= high
            for value, low, high in values
        )


class AlarmRule:
    """One combined heat/smoke node; two samples to trigger or clear."""

    def __init__(self):
        self.active = False
        self.hot_count = 0
        self.cool_count = 0

    def interrupt(self):
        # Missing/invalid data breaks consecutive samples, never clears an alarm.
        self.hot_count = self.cool_count = 0

    def update(self, reading):
        if not reading.valid():
            self.interrupt()
            return "SENSOR_FAULT"
        hot = reading.temperature > 60 and reading.smoke > 50
        cool = reading.temperature <= 50 and reading.smoke <= 30
        self.hot_count = self.hot_count + 1 if hot else 0
        self.cool_count = self.cool_count + 1 if cool else 0
        if not self.active and self.hot_count >= 2:
            self.active = True
            return "FIRE_RAISED"
        if self.active and self.cool_count >= 2:
            self.active = False
            return "FIRE_CLEARED"
        return "ALARM_ACTIVE" if self.active else "MONITORING"


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    fire_ticks: tuple = ()
    invalid_ticks: tuple = ()
    offline_ticks: tuple = ()
    operator_confirms: bool = True
    actuator_confirms: bool = True
    # Independent scenario labels, not inferred from detector predictions.
    expected_fire_zones: tuple = ()


SCENARIOS = (
    Scenario("normal", "Both zones remain normal."),
    Scenario("fire_event", "Kitchen fire-like readings, then recovery.",
             fire_ticks=(1, 2, 3), expected_fire_zones=("Kitchen",)),
    Scenario("failed_sensor", "Kitchen temperature becomes unavailable.",
             invalid_ticks=(1, 2, 3)),
    Scenario("lost_network", "Kitchen link fails during a fire-like event.",
             fire_ticks=(1, 2, 3), offline_ticks=(1, 2, 3, 4),
             expected_fire_zones=("Kitchen",)),
    Scenario("unconfirmed_operator", "Operator does not acknowledge the alarm.",
             fire_ticks=(1, 2, 3), operator_confirms=False,
             expected_fire_zones=("Kitchen",)),
    Scenario("unconfirmed_actuator", "Response device does not confirm operation.",
             fire_ticks=(1, 2, 3), actuator_confirms=False,
             expected_fire_zones=("Kitchen",)),
    Scenario("transient_spike", "One high sample without a sustained event.",
             fire_ticks=(2,)),
)


def make_reading(zone, tick, scenario):
    temperature, smoke, humidity = 25, 5, 50
    if zone == "Kitchen" and tick in scenario.fire_ticks:
        temperature, smoke, humidity = 80, 85, 40
    if zone == "Kitchen" and tick in scenario.invalid_ticks:
        temperature = None
    return Reading(zone, f"Sensor_{zone}", BASE_TIME + timedelta(seconds=tick),
                   temperature, smoke, humidity)


def run_scenario(scenario):
    """Seven one-second virtual ticks, two zones, no real sleeping or I/O."""
    started = perf_counter()
    events = []
    local = {zone: AlarmRule() for zone in ZONES}
    server = {zone: AlarmRule() for zone in ZONES}
    last_seen = {zone: -1 for zone in ZONES}
    network_faults = set()
    sensor_faults = set()
    undelivered_fire = set()
    response_faults = set()
    pending = {}

    def emit(tick, zone, stage, kind, message, **extra):
        events.append({
            "timestamp": (BASE_TIME + timedelta(seconds=tick)).isoformat(),
            "zone_id": zone, "stage": stage, "kind": kind,
            "message": message, **extra,
        })

    def request_response(tick, zone):
        emit(tick, zone, "operator/actuator", "RESPONSE_REQUESTED",
             "Request operator acknowledgement and response-device confirmation; "
             "all actions are simulated.")
        missing = []
        for label, confirms in (("OPERATOR", scenario.operator_confirms),
                                ("ACTUATOR", scenario.actuator_confirms)):
            if confirms:
                emit(tick, zone, "operator/actuator", f"{label}_CONFIRMED",
                     "Simulated confirmation received.")
            else:
                missing.append(label)
        if missing:
            pending[zone] = (tick, missing)

    for tick in range(7):
        for zone in ZONES:
            reading = make_reading(zone, tick, scenario)
            payload = asdict(reading)
            payload["timestamp"] = reading.timestamp.isoformat()
            emit(tick, zone, "sensor", "READING", "Simulated sensor sample.",
                 reading=payload)
            local_result = local[zone].update(reading)
            online = not (zone == "Kitchen" and tick in scenario.offline_ticks)

            if not online:
                emit(tick, zone, "local gateway", "LINK_UNAVAILABLE",
                     "Delivery failed; keep local detection and request manual contact.")
                if local_result == "FIRE_RAISED":
                    undelivered_fire.add(zone)
                    emit(tick, zone, "local gateway", "FIRE_RAISED",
                         "Local siren requested; remote delivery is unavailable.")
                elif local_result == "SENSOR_FAULT":
                    emit(tick, zone, "local gateway", "SENSOR_FAULT",
                         "Local trouble warning; inspect sensor. Status is unknown.")
                elif local_result == "FIRE_CLEARED":
                    emit(tick, zone, "local gateway", "FIRE_CLEARED",
                         "Two local safe samples; network trouble remains separate.")
                server[zone].interrupt()
                if tick - last_seen[zone] >= 2 and zone not in network_faults:
                    network_faults.add(zone)
                    emit(tick, zone, "server", "NETWORK_FAULT",
                         "Two missed samples; remote status unknown. Retain alarms, "
                         "warn operator, inspect link and use manual contact.")
            else:
                last_seen[zone] = tick
                emit(tick, zone, "gateway -> server", "DELIVERED",
                     "Reading delivered over the simulated link.")
                if zone in network_faults:
                    network_faults.remove(zone)
                    emit(tick, zone, "server", "LINK_RESTORED",
                         "Fresh reading received; resume evaluation.")
                # Synchronize a fire latched locally during the outage. A safe
                # sample must not erase an event the server has not seen yet.
                if zone in undelivered_fire and not server[zone].active:
                    server[zone].active = True
                    emit(tick, zone, "server", "ALARM_SYNCED",
                         "Local alarm retained across outage; request operator response.")
                    request_response(tick, zone)
                undelivered_fire.discard(zone)
                result = server[zone].update(reading)
                if result == "SENSOR_FAULT":
                    if zone not in sensor_faults:
                        emit(tick, zone, "server", "SENSOR_FAULT",
                             "Invalid reading: status unknown, retain active alarm, "
                             "warn operator and request inspection/manual monitoring.")
                    sensor_faults.add(zone)
                else:
                    if zone in sensor_faults:
                        sensor_faults.remove(zone)
                        emit(tick, zone, "server", "SENSOR_RESTORED",
                             "Valid sensor data received.")
                    emit(tick, zone, "server", result,
                         "Evaluate per-zone alarm rule; humidity is displayed only.")
                    if result == "FIRE_RAISED":
                        request_response(tick, zone)

            if zone in pending:
                requested_tick, missing = pending[zone]
                age = tick - requested_tick
                if age == 1:
                    emit(tick, zone, "operator/actuator", "RESPONSE_RETRY",
                         f"Retry once: {', '.join(missing)} confirmation missing. "
                         "Keep alarm response requested.")
                elif age == 2:
                    response_faults.add(zone)
                    emit(tick, zone, "operator/actuator", "RESPONSE_UNCONFIRMED",
                         f"{', '.join(missing)} still unconfirmed. Do not claim success. "
                         "Keep warning; request manual evacuation/response and device inspection.")
                    del pending[zone]

    raised = [event for event in events if event["kind"] == "FIRE_RAISED"]
    false_alarms = sum(event["zone_id"] not in scenario.expected_fire_zones for event in raised)
    expected_alerts = [event for event in raised if event["zone_id"] in scenario.expected_fire_zones]
    server_alerts = [event for event in events if event["stage"] == "server"
                     and event["kind"] in ("FIRE_RAISED", "ALARM_SYNCED")
                     and event["zone_id"] in scenario.expected_fire_zones]
    delay = None
    server_delay = None
    if scenario.expected_fire_zones and expected_alerts:
        onset = BASE_TIME + timedelta(seconds=min(scenario.fire_ticks))
        delay = (datetime.fromisoformat(expected_alerts[0]["timestamp"]) - onset).total_seconds()
        if server_alerts:
            server_delay = (datetime.fromisoformat(server_alerts[0]["timestamp"]) - onset).total_seconds()
    return {
        "scenario": scenario.name, "description": scenario.description,
        "trigger_rule": TRIGGER_RULE, "simulated_alert_delay_seconds": delay,
        "simulated_server_awareness_delay_seconds": server_delay,
        "false_fire_alarms": false_alarms, "fire_alert_count": len(raised),
        "missed_expected_fire": bool(scenario.expected_fire_zones and not expected_alerts),
        "measured_execution_ms": round((perf_counter() - started) * 1000, 3),
        "final_zone_status": {
            zone: {"local_alarm_active": local[zone].active,
                   "server_alarm_active": server[zone].active,
                   "sensor_fault": zone in sensor_faults,
                   "network_fault": zone in network_faults,
                   "response_unconfirmed": zone in response_faults}
            for zone in ZONES
        },
        "events": events,
    }


def render_report(results):
    lines = [
        "# Connected fire-safety simulation results", "",
        "Generated by `python3 -B connected_simulation.py --output-dir reports`.", "",
        "All readings, links, confirmations and timestamps are simulated. Delay is",
        "virtual elapsed time from the first scripted fire-like reading to the first",
        "local or server fire alert. Execution time is measured wall-clock code time;",
        "it is not network latency or physical detection delay. N/A means no fire was expected.", "",
        f"Trigger rule: {TRIGGER_RULE}, independently in each zone.",
        "Clear rule: temperature <= 50 C AND smoke <= 30%, twice consecutively.", "",
        "False alarms count FIRE_RAISED events in zones without a scripted sustained fire.",
        "Trouble warnings are reported separately from fire alarms. These small synthetic",
        "scenarios do not measure real fire-detection accuracy.", "",
        "| Scenario | Trigger rule | Simulated first alert (s) | Simulated server awareness (s) | False fire alarms | Fire alerts | Missed expected fire | Measured execution (ms) |",
        "|---|---|---:|---:|---:|---:|---|---:|",
    ]
    for result in results:
        delay = result["simulated_alert_delay_seconds"]
        server_delay = result["simulated_server_awareness_delay_seconds"]
        lines.append(f"| {result['scenario']} | Both thresholds, 2 samples | {delay if delay is not None else 'N/A'} | "
                     f"{server_delay if server_delay is not None else 'N/A'} | "
                     f"{result['false_fire_alarms']} | {result['fire_alert_count']} | "
                     f"{result['missed_expected_fire']} | {result['measured_execution_ms']} |")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=[item.name for item in SCENARIOS])
    parser.add_argument("--output-dir", type=Path,
                        help="Optionally write timestamped JSON, text and results table.")
    args = parser.parse_args()
    selected = [item for item in SCENARIOS if args.scenario in (None, item.name)]
    results = [run_scenario(item) for item in selected]
    output = []
    for result in results:
        output.append(f"\nScenario: {result['scenario']} — {result['description']}")
        for event in result["events"]:
            line = (f"{event['timestamp']} zone={event['zone_id']} "
                    f"[{event['stage']}] {event['kind']}: {event['message']}")
            if "reading" in event:
                line += " " + json.dumps(event["reading"], ensure_ascii=False)
            output.append(line)
    text = "\n".join(output) + "\n"
    print(text)
    print(render_report(results))
    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "simulation_output.txt").write_text(text, encoding="utf-8")
        (args.output_dir / "simulation_results.json").write_text(
            json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (args.output_dir / "RESULTS.md").write_text(render_report(results), encoding="utf-8")


if __name__ == "__main__":
    main()
