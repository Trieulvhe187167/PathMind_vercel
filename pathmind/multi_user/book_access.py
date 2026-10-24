"""Book access resolver: every user (admin included) sees only their own books.

There is no shared catalogue and no per-book permission; what a user may do
with books is decided by role and subscription plan (entitlements) alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pathmind.book.learning_overlay import BookLearningOverlay
from pathmind.book.models import Book, Progress
from pathmind.book.storage import BookStorage, get_book_storage

if TYPE_CHECKING:
    from pathmind.book.engine import BookEngine

BookPermissionLevel = Literal["none", "read", "edit"]
BookSource = Literal["own", "shared"]


@dataclass(frozen=True, slots=True)
class ResolvedBook:
    """A Book bound to its canonical store and the caller's learning store."""

    engine: BookEngine
    source: BookSource
    permission: BookPermissionLevel
    can_edit: bool
    can_delete: bool
    learning: BookStorage | BookLearningOverlay

    @property
    def is_shared(self) -> bool:
        return self.source == "shared"

    def capabilities(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "permission": self.permission,
            "can_edit": self.can_edit,
            "can_delete": self.can_delete,
        }

    def load_progress(self, book_id: str) -> Progress:
        progress = self.learning.load_progress(book_id)
        return progress or Progress(book_id=book_id)

    def reading_summary(self, book: Book) -> dict[str, Any]:
        progress = self.learning.load_progress(book.id)
        total = max(0, book.page_count)
        if progress is None:
            return {
                "current_page_id": "",
                "visited_pages": 0,
                "total_pages": total,
                "percent": 0,
            }
        visited = len(set(progress.visited_page_ids))
        return {
            "current_page_id": progress.current_page_id,
            "visited_pages": visited,
            "total_pages": total,
            "percent": round(min(100.0, visited * 100.0 / total)) if total else 0,
        }


def can_create_book() -> bool:
    # Creation limits come from the subscription plan (entitlements), not here.
    return True


def resolve_book(book_id: str) -> ResolvedBook | None:
    """Resolve one of the caller's own books; None for unknown ids."""

    from pathmind.book.engine import get_book_engine

    own_storage = get_book_storage()
    if not own_storage.book_exists(book_id):
        return None
    return ResolvedBook(
        engine=get_book_engine(),
        source="own",
        permission="edit",
        can_edit=True,
        can_delete=True,
        learning=own_storage,
    )


def accessible_books() -> list[tuple[Book, ResolvedBook]]:
    """List the caller's own books, newest first."""

    from pathmind.book.engine import get_book_engine

    own_storage = get_book_storage()
    own_engine = get_book_engine()
    results = [
        (
            book,
            ResolvedBook(
                engine=own_engine,
                source="own",
                permission="edit",
                can_edit=True,
                can_delete=True,
                learning=own_storage,
            ),
        )
        for book in own_engine.list_books()
    ]
    results.sort(key=lambda item: item[0].updated_at, reverse=True)
    return results


__all__ = [
    "ResolvedBook",
    "accessible_books",
    "can_create_book",
    "resolve_book",
]
