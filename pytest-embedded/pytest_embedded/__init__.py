"""A pytest plugin that designed for embedded testing."""

print('THIS IS A ENTRY POINT')
from .app import App
from .dut import Dut
from .dut_factory import DutFactory

__all__ = ['App', 'Dut', 'DutFactory']

__version__ = '1.11.5'
