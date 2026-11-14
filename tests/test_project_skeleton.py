from pathlib import Path


def test_project_has_architecture_document() -> None:
    root = Path(__file__).resolve().parents[1]
    assert (root / "docs" / "architecture.md").is_file()


def test_example_verification_url_points_to_the_served_page() -> None:
    root = Path(__file__).resolve().parents[1]
    example = (root / ".env.example").read_text(encoding="utf-8")
    assert "EMAIL_VERIFICATION_URL=http://localhost:8000/verify-email" in example
    assert "EMAIL_VERIFICATION_URL=http://localhost:8000/auth/verify-email" not in example
