import os
import sys
import pytest
from colorama import Fore, Style
import networkx as nx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from epicPlanner import createDependencyOutput, checkDependenciesResolved


class TestCreateDependencyOutput:
    def test_empty_dependencies(self):
        graph = nx.DiGraph()
        assert createDependencyOutput(graph, []) == "[]"

    def test_single_done_dependency(self):
        graph = nx.DiGraph()
        graph.add_node("PROJ-1", status="Done")
        result = createDependencyOutput(graph, ["PROJ-1"])
        assert "PROJ-1" in result
        assert Fore.GREEN in result

    def test_single_open_dependency(self):
        graph = nx.DiGraph()
        graph.add_node("PROJ-1", status="In Progress")
        result = createDependencyOutput(graph, ["PROJ-1"])
        assert "PROJ-1" in result
        assert Fore.RED in result

    def test_multiple_dependencies(self):
        graph = nx.DiGraph()
        graph.add_node("A-1", status="Done")
        graph.add_node("A-2", status="Open")
        result = createDependencyOutput(graph, ["A-1", "A-2"])
        assert "A-1" in result
        assert "A-2" in result
        assert Fore.GREEN in result
        assert Fore.RED in result

    def test_brackets_present(self):
        graph = nx.DiGraph()
        graph.add_node("X-1", status="Done")
        result = createDependencyOutput(graph, ["X-1"])
        assert result.startswith("[")
        assert result.endswith(Style.RESET_ALL + "]")


class TestCheckDependenciesResolved:
    def test_all_resolved(self):
        # Green only = all resolved
        deps = f"[{Fore.GREEN}PROJ-1{Style.RESET_ALL}, {Fore.GREEN}PROJ-2{Style.RESET_ALL}]"
        assert checkDependenciesResolved(deps) is True

    def test_has_unresolved(self):
        deps = f"[{Fore.RED}PROJ-1{Style.RESET_ALL}]"
        assert checkDependenciesResolved(deps) is False

    def test_empty_deps(self):
        assert checkDependenciesResolved("[]") is True

    def test_mixed(self):
        deps = f"[{Fore.GREEN}A-1{Style.RESET_ALL}, {Fore.RED}A-2{Style.RESET_ALL}]"
        assert checkDependenciesResolved(deps) is False
