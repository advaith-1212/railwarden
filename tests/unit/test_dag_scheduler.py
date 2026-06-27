from __future__ import annotations

import pytest

from lfg.config.models import WorkPackage
from lfg.errors import ConfigurationError
from lfg.scheduler.dag import Dag, validate_dag


def test_cycle_detection() -> None:
    packages = {
        "A": WorkPackage("A", "A", "", ("B",)),
        "B": WorkPackage("B", "B", "", ("A",)),
    }
    with pytest.raises(ConfigurationError):
        validate_dag(packages)


def test_dependency_release_order() -> None:
    packages = {
        "A": WorkPackage("A", "A", ""),
        "B": WorkPackage("B", "B", "", ("A",)),
        "C": WorkPackage("C", "C", "", ("B",)),
    }
    assert Dag(packages).topological() == ("A", "B", "C")
    assert Dag(packages).critical_path() == ("A", "B", "C")
