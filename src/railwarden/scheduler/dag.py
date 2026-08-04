from __future__ import annotations

from dataclasses import dataclass

from railwarden.config.models import WorkPackage
from railwarden.errors import ConfigurationError


@dataclass(frozen=True)
class Dag:
    packages: dict[str, WorkPackage]

    def topological(self) -> tuple[str, ...]:
        validate_dag(self.packages)
        ordered: list[str] = []
        visited: set[str] = set()

        def visit(package_id: str) -> None:
            if package_id in visited:
                return
            for dependency in self.packages[package_id].dependencies:
                visit(dependency)
            visited.add(package_id)
            ordered.append(package_id)

        for package_id in sorted(self.packages):
            visit(package_id)
        return tuple(ordered)

    def critical_path(self) -> tuple[str, ...]:
        self.topological()
        memo: dict[str, tuple[str, ...]] = {}

        def path_to(package_id: str) -> tuple[str, ...]:
            if package_id in memo:
                return memo[package_id]
            deps = self.packages[package_id].dependencies
            if not deps:
                memo[package_id] = (package_id,)
                return memo[package_id]
            best = max((path_to(dep) for dep in deps), key=len)
            memo[package_id] = (*best, package_id)
            return memo[package_id]

        if not self.packages:
            return ()
        return max((path_to(package_id) for package_id in self.packages), key=len)


def validate_dag(packages: dict[str, WorkPackage]) -> None:
    known = set(packages)
    for package in packages.values():
        unknown = set(package.dependencies) - known
        if unknown:
            raise ConfigurationError(
                f"{package.package_id} has unknown dependencies: {sorted(unknown)}"
            )
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(package_id: str) -> None:
        if package_id in visiting:
            raise ConfigurationError(f"Dependency cycle detected at {package_id}")
        if package_id in visited:
            return
        visiting.add(package_id)
        for dependency in packages[package_id].dependencies:
            visit(dependency)
        visiting.remove(package_id)
        visited.add(package_id)

    for package_id in packages:
        visit(package_id)
