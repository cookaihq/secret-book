import re
import tomllib
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]


def test_product_version_is_2_0_0_and_all_version_sources_match():
    project_version = tomllib.loads(
        (REPO / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]["version"]
    lock_version = next(
        package["version"] for package in tomllib.loads(
            (REPO / "uv.lock").read_text(encoding="utf-8")
        )["package"] if package["name"] == "secret-book"
    )
    skill = (REPO / "SKILL.md").read_text(encoding="utf-8")
    frontmatter_version = re.search(r"^version:\s*([^\s]+)$", skill, re.MULTILINE).group(1)
    description_version = re.search(r"^\s*v([0-9]+\.[0-9]+\.[0-9]+)｜", skill, re.MULTILINE).group(1)

    assert project_version == "2.0.0"
    assert lock_version == project_version
    assert frontmatter_version == project_version
    assert description_version == project_version
