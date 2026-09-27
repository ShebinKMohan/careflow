import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# The agent's Dockerfile, compose file and deployment docs were not ported (see SOURCE.md),
# so only the dependency check remains here.


def test_runtime_dependencies_do_not_include_unused_multipart_parser() -> None:
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    dependencies = project["project"]["dependencies"]

    assert all(not dependency.lower().startswith("python-multipart") for dependency in dependencies)
