"""
Local conftest for the screenshots sub-suite.

The root tests/conftest.py defines several autouse async fixtures
(seed_test_user, db_conn) that assume pytest-asyncio owns the event loop.
Those conflict with uvicorn running its own event loop in a background thread.

By re-declaring seed_test_user here as a plain (sync, non-autouse) no-op,
pytest uses this closer-scope fixture and the root autouse one is suppressed
for all tests in this directory.
"""

import pytest


@pytest.fixture(autouse=True)
def seed_test_user():
    """No-op override: screenshot tests manage their own DB via live_server_url."""
    pass
