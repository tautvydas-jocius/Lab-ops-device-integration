from django.core.management.base import BaseCommand
from devices.models import ParserSchema, Device
SCHEMAS = [
 ("mt-sics", r"^S\s+(?P<status>S|D)\s+(?P<value>[+-]?\d+(?:\.\d+)?)\s+(?P<unit>\S+)$", "status", "equals", "S", "Mettler Toledo MT-SICS S/S D lines"),
 ("sartorius-sbi", r"^(?P<status>[A-Z])\s*(?P<sign>[+-])\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>\S*)\s*$", "unit", "present", "", "Sartorius SBI; unit marks stable readings"),
 ("ohaus", r"^\s*(?P<value>[+-]?\d+(?:\.\d+)?)\s+(?P<unit>\S+)\s*(?P<unstable>\?)?\s*$", "unstable", "absent", "", "Ohaus continuous output; question mark marks unstable"),
]
class Command(BaseCommand):
    help = "Create demo parser schemas and scales"
    def handle(self, *args, **kwargs):
        schemas = {}
        for name, pattern, group, rule, values, description in SCHEMAS:
            schemas[name], _ = ParserSchema.objects.get_or_create(name=name, version=1, defaults={"pattern": pattern, "stable_group": group, "stable_rule": rule, "stable_values": values, "description": description})
        devices = [
            ("bal-01", "Demo Mettler Toledo", "mt-sics", 9600, "SI\r\n", 1000),
            ("bal-02", "Demo Sartorius", "sartorius-sbi", 9600, "\x1bP\r\n", 2000),
            ("bal-03", "Demo Ohaus", "ohaus", 9600, "", 1000),
        ]
        for ident, name, parser, baud, command, interval in devices:
            Device.objects.get_or_create(device_id=ident, defaults={"name": name, "manufacturer": name.split()[1], "model": name.split()[-1], "parser": schemas[parser], "baud": baud, "poll_command": command, "poll_interval_ms": interval})
