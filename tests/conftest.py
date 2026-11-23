import socket

import pytest

from resibo.config import Settings
from tests.helpers import FakeLLM  # noqa: F401  (re-exported for tests)


@pytest.fixture(autouse=True)
def _no_key_no_network(request, monkeypatch):
    """AD-19: no API key and no outbound connections in tests not marked ``live``."""
    if request.node.get_closest_marker("live"):
        return
    # the developer's shell must not change defaults the tests rely on
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)

    def blocked(self, address, *args, **kwargs):
        raise RuntimeError(f"network access is blocked in tests (connect to {address!r})")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
