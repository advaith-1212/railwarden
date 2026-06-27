from __future__ import annotations

from lfg.config.models import PlanDiff


def test_plan_diff_shape() -> None:
    diff = PlanDiff(added=("B",), removed=("A",), changed_dependencies=("C",))
    assert diff.added == ("B",)
    assert diff.removed == ("A",)
    assert diff.changed_dependencies == ("C",)
