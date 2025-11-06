import pytest
from mr_box_peripheral_board.proxy import SerialProxy
import time


# Fixture to create and initialize the SerialProxy instance
# Ensures that only one proxy object exists at any given time
@pytest.fixture(scope="module")
def proxy():
    proxy_instance = SerialProxy()
    time.sleep(2)  # Allow time for initialization
    yield proxy_instance  # Provide the instance to the test
    # Teardown logic to ensure no lingering proxy connections
    del proxy_instance


# Test to verify if the proxy connection returns the expected RAM free value
def test_connection(proxy):
    assert proxy.ram_free() == 490, "RAM free value did not match expected 490"


# Test to set the Z-stage position and verify the change
# Test to ensure the Z-stage returns to its home position correctly after
def test_zstage_home(proxy):
    proxy.zstage.position = 10  # Change position first
    assert proxy.zstage.position == 10, "Z-stage position did not set correctly"
    proxy.zstage.home()  # Home the Z-stage
    assert proxy.zstage.position == 0, "Z-stage did not return to home position"


# Run tests when executed as a standalone script
if __name__ == "__main__":
    pytest.main()
