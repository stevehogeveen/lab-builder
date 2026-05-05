from __future__ import annotations

from typing import Any


class HpeIloRedfishAdapter:
    def __init__(self, client: Any):
        self.client = client

    def license_status(self) -> dict[str, Any]:
        return self.client.get_license_status_best_effort()

    def apply_snmp_policy(self, **kwargs: Any) -> dict[str, Any]:
        return self.client.configure_snmp_policy_best_effort(**kwargs)

    def apply_alert_destinations(self, **kwargs: Any) -> dict[str, Any]:
        return self.client.configure_snmp_alert_destinations_best_effort(**kwargs)

    def apply_ipv6_policy(self) -> dict[str, Any]:
        return self.client.configure_ipv6_policy_best_effort()

    def apply_time_policy(self, **kwargs: Any) -> dict[str, Any]:
        return self.client.configure_sntp_policy_best_effort(**kwargs)
