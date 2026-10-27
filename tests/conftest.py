import pytest


@pytest.fixture
def anyio_backend():
    # The server runs under asyncio (MCPServer.run); trio is not needed.
    return 'asyncio'
