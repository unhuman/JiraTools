import os
import sys
import pytest
from unittest.mock import patch, MagicMock
from colorama import Fore, Style

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from epicStatus import getTicketColor


class TestGetTicketColor:
    def test_done_status(self):
        color = getTicketColor("Done")
        assert Fore.GREEN in color
        assert Style.BRIGHT in color

    def test_closed_status(self):
        color = getTicketColor("Closed")
        assert Fore.GREEN in color

    def test_withdrawn(self):
        color = getTicketColor("Withdrawn")
        assert Fore.GREEN in color

    def test_in_progress(self):
        color = getTicketColor("In Progress")
        assert Fore.YELLOW in color

    def test_open_status(self):
        color = getTicketColor("Open")
        assert Fore.YELLOW in color

    def test_case_sensitivity_withdrawn(self):
        # "withdrawn" is checked with .lower()
        color = getTicketColor("withdrawn")
        assert Fore.GREEN in color
