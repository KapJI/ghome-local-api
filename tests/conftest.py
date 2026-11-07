"""Shared pytest settings."""

import aiohttp


def pytest_report_header() -> str:
    """Show the aiohttp version the tests run with, such as the lowest allowed one."""
    return f"aiohttp {aiohttp.__version__}"
