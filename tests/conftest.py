"""
tests/conftest.py - Shared pytest fixtures for DucksDrive test suite.
"""

import os

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp():
    """Ensures an offscreen Qt application exists for widget and signal tests."""
    app = QApplication.instance()
    if app is None:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        app = QApplication([])
    return app
