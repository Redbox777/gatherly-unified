from pathlib import Path


def test_project_has_architecture_document() -> None:
    root = Path(__file__).resolve().parents[1]
    assert (root / "docs" / "architecture.md").is_file()
