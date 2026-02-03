"""Shared pytest configuration for beavr-network."""


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')")
    config.addinivalue_line("markers", "integration: marks tests requiring full environment")
    config.addinivalue_line("markers", "hardware: marks tests requiring physical hardware")
