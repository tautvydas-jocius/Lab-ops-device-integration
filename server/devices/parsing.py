import re
from decimal import Decimal, InvalidOperation

def parse(line, schema):
    raw = (line or "").rstrip("\r\n")
    try:
        match = re.match(schema.pattern, raw)
        if not match:
            return {"ok": False, "value": None, "unit": "", "stable": None, "fields": {}, "error": "line did not match parser schema"}
        groups = {k: (v or "").strip() for k, v in match.groupdict().items()}
        number = groups.get("value", "")
        sign = groups.get("sign", "")
        if sign in ("+", "-") and not number.startswith(("+", "-")):
            number = sign + number
        value = Decimal(number)
        unit = groups.get("unit", "")
        stable = None
        if schema.stable_group:
            marker = groups.get(schema.stable_group, "")
            vals = [v.strip() for v in schema.stable_values.split(",") if v.strip()]
            if schema.stable_rule == "equals": stable = marker in vals
            elif schema.stable_rule == "present": stable = bool(marker)
            else: stable = not bool(marker)
        return {"ok": True, "value": value, "unit": unit, "stable": stable, "fields": groups, "error": ""}
    except (InvalidOperation, ValueError) as exc:
        return {"ok": False, "value": None, "unit": "", "stable": None, "fields": {}, "error": f"invalid decimal value: {exc}"}
