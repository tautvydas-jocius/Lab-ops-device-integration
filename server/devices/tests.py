from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from unittest.mock import patch
from .models import Device, ParserSchema, Reading
from .parsing import parse
from .mqtt import config_payload

class DeviceIntegrationTests(TestCase):
    def setUp(self):
        self.mt = ParserSchema.objects.create(name="mt", version=1, pattern=r"^S\s+(?P<status>S|D)\s+(?P<value>[+-]?\d+(?:\.\d+)?)\s+(?P<unit>\S+)$", stable_group="status", stable_rule="equals", stable_values="S")
        self.sart = ParserSchema.objects.create(name="sart", version=1, pattern=r"^(?P<status>[A-Z])\s*(?P<sign>[+-])\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>\S*)\s*$", stable_group="unit", stable_rule="present")
        self.ohaus = ParserSchema.objects.create(name="ohaus", version=1, pattern=r"^\s*(?P<value>[+-]?\d+(?:\.\d+)?)\s+(?P<unit>\S+)\s*(?P<unstable>\?)?\s*$", stable_group="unstable", stable_rule="absent")
    def test_mt_stable_dynamic_and_garbage(self):
        self.assertTrue(parse("S S      12.3456 g\r\n", self.mt)["stable"])
        self.assertFalse(parse("S D      12.3456 g", self.mt)["stable"])
        self.assertFalse(parse("garbage", self.mt)["ok"])
    def test_sartorius_sign_stable_unstable(self):
        good = parse("N     +   12.345 g  ", self.sart)
        self.assertEqual(str(good["value"]), "12.345"); self.assertTrue(good["stable"])
        bad = parse("N     -   12.345   ", self.sart)
        self.assertEqual(str(bad["value"]), "-12.345"); self.assertFalse(bad["stable"])
    def test_ohaus_stable_unstable_and_sign(self):
        self.assertTrue(parse("    12.345 g    ", self.ohaus)["stable"])
        self.assertFalse(parse("    -12.345 g    ?", self.ohaus)["stable"])
    @patch("devices.mqtt.publish_config")
    def test_config_version_and_payload(self, publish):
        d = Device.objects.create(device_id="bal-test", name="Test", parser=self.mt)
        self.assertEqual(config_payload(d)["config_version"], 1)
        d.poll_interval_ms = 2500; d.save()
        self.assertEqual(d.config_version, 2)
        self.assertEqual(config_payload(d)["poll_interval_ms"], 2500)
    def test_referenced_schema_immutable(self):
        d = Device.objects.create(device_id="bal-test", name="Test", parser=self.mt)
        Reading.objects.create(device=d, parser=self.mt, raw_line="S S 1 g", ok=True)
        self.mt.pattern = r"different"
        with self.assertRaisesMessage(ValidationError, "create a new version"):
            self.mt.save()
