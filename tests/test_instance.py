"""Single instance: a named mutex allows only one Uncle Farmer per Windows session."""

import sys
import uuid

import pytest

from unclefarmer import instance

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows named mutex")


def test_only_one_instance_can_hold_the_mutex():
    name = "Local\\UncleFarmerTest-" + uuid.uuid4().hex
    first, second = instance.InstanceLock(name), instance.InstanceLock(name)
    try:
        assert first.acquire()
        assert not second.acquire()
        first.release()
        assert second.acquire()
    finally:
        first.release()
        second.release()
