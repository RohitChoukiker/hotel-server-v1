"""Repository-wide safety guard tests."""

import unittest
from pathlib import Path


class ArchitectureGuardTests(unittest.TestCase):
    """Ensure forbidden recommendation technologies cannot creep into the codebase."""

    def test_no_vector_or_embedding_implementation(self) -> None:
        root = Path(__file__).parents[2] / "app"
        forbidden = ("pgvector", "vector database", "embedding-based", "semantic vector")
        content = "\n".join(
            path.read_text(encoding="utf-8").casefold()
            for path in root.rglob("*.py")
            if "test" not in path.parts
        )
        for term in forbidden:
            self.assertNotIn(term, content)

    def test_main_contains_no_database_queries(self) -> None:
        main = (Path(__file__).parents[2] / "app" / "main.py").read_text(encoding="utf-8")
        self.assertNotIn("select(", main)
        self.assertNotIn("session.execute", main)

    def test_controllers_do_not_raise_http_exception(self) -> None:
        api_root = Path(__file__).parents[2] / "app" / "api"
        content = "\n".join(path.read_text(encoding="utf-8") for path in api_root.rglob("*.py"))
        self.assertNotIn("HTTPException", content)

    def test_exact_external_ids_are_text(self) -> None:
        models = (Path(__file__).parents[2] / "app" / "models" / "hotel.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("source_hotel_id: Mapped[str] = mapped_column(Text", models)
        self.assertIn("source_review_id: Mapped[str] = mapped_column(Text", models)


if __name__ == "__main__":
    unittest.main()

