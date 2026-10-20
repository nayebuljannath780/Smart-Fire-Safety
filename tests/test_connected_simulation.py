"""Behavior checks for simulation assumptions, not real sensor certification."""

import math
import unittest
from dataclasses import replace

from connected_simulation import (
    AlarmRule, BASE_TIME, Reading, SCENARIOS, Scenario, run_scenario,
)


def sample(temperature=25, smoke=5, humidity=50):
    return Reading("Kitchen", "Sensor_Kitchen", BASE_TIME,
                   temperature, smoke, humidity)


class AlarmRuleTests(unittest.TestCase):
    def test_trigger_needs_both_sensors_and_two_consecutive_samples(self):
        rule = AlarmRule()
        for reading in (sample(100, 5), sample(25, 90), sample(60, 90), sample(90, 50)):
            self.assertEqual(rule.update(reading), "MONITORING")
        self.assertEqual(rule.update(sample(61, 51)), "MONITORING")
        self.assertEqual(rule.update(sample(61, 51)), "FIRE_RAISED")

    def test_spike_does_not_trigger_and_breaks_consecutiveness(self):
        rule = AlarmRule()
        for reading in (sample(80, 85), sample(), sample(80, 85)):
            self.assertEqual(rule.update(reading), "MONITORING")

    def test_clear_requires_two_safe_samples_and_lower_thresholds(self):
        rule = AlarmRule()
        rule.update(sample(80, 85))
        rule.update(sample(80, 85))
        self.assertEqual(rule.update(sample(55, 35)), "ALARM_ACTIVE")
        self.assertEqual(rule.update(sample(50, 30)), "ALARM_ACTIVE")
        self.assertEqual(rule.update(sample(50, 30)), "FIRE_CLEARED")

    def test_bad_data_never_clears_active_alarm(self):
        rule = AlarmRule()
        rule.update(sample(80, 85))
        rule.update(sample(80, 85))
        rule.update(sample())
        self.assertEqual(rule.update(sample(None)), "SENSOR_FAULT")
        self.assertTrue(rule.active)
        self.assertEqual(rule.update(sample()), "ALARM_ACTIVE")
        self.assertEqual(rule.update(sample()), "FIRE_CLEARED")

    def test_invalid_values_are_faults(self):
        for value in (None, "80", True, math.nan, math.inf, -41, 151):
            with self.subTest(value=value):
                self.assertEqual(AlarmRule().update(sample(value)), "SENSOR_FAULT")
        for reading in (sample(smoke=-1), sample(smoke=101), sample(humidity=None)):
            self.assertFalse(reading.valid())

    def test_interruption_prevents_two_samples_across_a_gap(self):
        rule = AlarmRule()
        rule.update(sample(80, 85))
        rule.interrupt()
        self.assertEqual(rule.update(sample(80, 85)), "MONITORING")


class ConnectedScenarioTests(unittest.TestCase):
    def run_named(self, name):
        return run_scenario(next(item for item in SCENARIOS if item.name == name))

    def test_all_scenarios_have_metadata_and_expected_metrics(self):
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario.name):
                result = run_scenario(scenario)
                self.assertEqual(result["false_fire_alarms"], 0)
                self.assertFalse(result["missed_expected_fire"])
                expected = 1.0 if scenario.expected_fire_zones else None
                self.assertEqual(result["simulated_alert_delay_seconds"], expected)
                readings = [event for event in result["events"] if event["kind"] == "READING"]
                self.assertEqual(len(readings), 14)
                self.assertEqual({event["zone_id"] for event in readings}, {"Kitchen", "Office"})
                for event in result["events"]:
                    self.assertIn("zone_id", event)
                    self.assertTrue(event["timestamp"].endswith("+00:00"))
                for event in readings:
                    self.assertEqual(event["timestamp"], event["reading"]["timestamp"])
                    self.assertEqual(event["zone_id"], event["reading"]["zone_id"])

    def test_fire_is_isolated_to_kitchen_and_clears(self):
        events = self.run_named("fire_event")["events"]
        alarms = [event for event in events if event["kind"] == "FIRE_RAISED"]
        self.assertEqual(len(alarms), 1)
        self.assertEqual(alarms[0]["zone_id"], "Kitchen")
        self.assertTrue(any(event["kind"] == "FIRE_CLEARED" for event in events))
        self.assertTrue(any(event["kind"] == "ACTUATOR_CONFIRMED" for event in events))

    def test_failed_sensor_warns_and_recovers_without_fire(self):
        result = self.run_named("failed_sensor")
        kinds = [event["kind"] for event in result["events"]]
        self.assertIn("SENSOR_FAULT", kinds)
        self.assertIn("SENSOR_RESTORED", kinds)
        self.assertEqual(result["fire_alert_count"], 0)

    def test_lost_link_local_fallback_and_alarm_delivery_after_recovery(self):
        result = self.run_named("lost_network")
        events = result["events"]
        alarm = next(event for event in events if event["kind"] == "FIRE_RAISED")
        self.assertEqual(alarm["stage"], "local gateway")
        self.assertEqual(result["simulated_server_awareness_delay_seconds"], 4.0)
        kinds = [event["kind"] for event in events]
        self.assertIn("NETWORK_FAULT", kinds)
        self.assertIn("LINK_RESTORED", kinds)
        self.assertIn("ALARM_SYNCED", kinds)
        self.assertIn("FIRE_CLEARED", kinds)
        # Link fails at tick 1, server warns after two missed samples at tick 2.
        fault = next(event for event in events if event["kind"] == "NETWORK_FAULT")
        self.assertEqual(fault["timestamp"], "2026-10-21T00:00:02+00:00")
        self.assertFalse(any(event["kind"] == "DELIVERED" and event["zone_id"] == "Kitchen"
                             and 1 <= int(event["timestamp"][17:19]) <= 4 for event in events))

    def test_missing_confirmations_retry_and_escalate_separately(self):
        for scenario, missing, present in (
            ("unconfirmed_operator", "OPERATOR", "ACTUATOR"),
            ("unconfirmed_actuator", "ACTUATOR", "OPERATOR"),
        ):
            with self.subTest(scenario=scenario):
                result = self.run_named(scenario)
                events = result["events"]
                self.assertTrue(result["final_zone_status"]["Kitchen"]["response_unconfirmed"])
                self.assertFalse(result["final_zone_status"]["Office"]["response_unconfirmed"])
                kinds = [event["kind"] for event in events]
                self.assertIn("RESPONSE_RETRY", kinds)
                self.assertIn("RESPONSE_UNCONFIRMED", kinds)
                self.assertNotIn(f"{missing}_CONFIRMED", kinds)
                self.assertIn(f"{present}_CONFIRMED", kinds)
                escalation = next(event for event in events if event["kind"] == "RESPONSE_UNCONFIRMED")
                self.assertIn(missing, escalation["message"])

    def test_metrics_count_an_unexpected_fire_as_a_false_alarm(self):
        mislabeled = Scenario("metric_check", "Intentional test of false-alarm counter.",
                              fire_ticks=(1, 2, 3))
        result = run_scenario(mislabeled)
        self.assertEqual(result["false_fire_alarms"], 1)

    def test_metrics_report_missed_expected_fire(self):
        result = run_scenario(Scenario("missed", "No high readings despite expected label.",
                                      expected_fire_zones=("Kitchen",)))
        self.assertTrue(result["missed_expected_fire"])
        self.assertIsNone(result["simulated_alert_delay_seconds"])

    def test_invalid_readings_during_fire_do_not_clear_alarm(self):
        scenario = replace(next(item for item in SCENARIOS if item.name == "fire_event"),
                           invalid_ticks=(3, 4, 5, 6))
        events = run_scenario(scenario)["events"]
        self.assertTrue(any(event["kind"] == "FIRE_RAISED" for event in events))
        self.assertFalse(any(event["kind"] == "FIRE_CLEARED" for event in events))


if __name__ == "__main__":
    unittest.main()
