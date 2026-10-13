"""pp switch public Python SDK: secretless capability invocation.

    from ppswitch import PPSwitchClient

    pp = PPSwitchClient.from_local_broker()          # mock transport (tests)
    pp = PPSwitchClient.from_http(url, token=...)    # real control plane

    result = pp.invoke("crm.get_customer", {"customer_id": "C-1024"})

The SDK deliberately exposes ONLY secretless operations: ``invoke``,
``list_capabilities`` and ``profile_status``. There is no ``get_secret``,
no ``export_secret``, no way to pass credentials, target URLs or arbitrary
headers - by design and by test (docs/18-SECRETLESS-DEVELOPMENT.md).

Security status (be honest when reusing this code): the HTTP transport
authenticates with a synthetic dev token against a localhost control
plane. It is NOT yet a production-grade authenticated SDK; do not present
it as one until workload identity / OAuth audiences land.
"""

from ppswitch.client import (
    HttpTransport,
    InvokeResult,
    LocalBrokerTransport,
    PPSwitchClient,
    PPSwitchError,
    Transport,
)

__all__ = [
    "HttpTransport",
    "InvokeResult",
    "LocalBrokerTransport",
    "PPSwitchClient",
    "PPSwitchError",
    "Transport",
]
