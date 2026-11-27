"""In-memory stock levels."""

from __future__ import annotations


class Inventory:
    """Item names mapped to positive quantities."""

    def __init__(self) -> None:
        self._items: dict[str, int] = {}

    @staticmethod
    def _clean_name(name: str) -> str:
        cleaned = (name or "").strip()
        if not cleaned:
            raise ValueError("item name must not be empty")
        return cleaned

    def add(self, name: str, qty: int = 1) -> None:
        """Add ``qty`` units of ``name`` (qty must be positive)."""
        name = self._clean_name(name)
        if qty <= 0:
            raise ValueError("quantity must be positive")
        self._items[name] = self._items.get(name, 0) + qty

    def remove(self, name: str, qty: int = 1) -> None:
        """Remove ``qty`` units; the item disappears when it reaches zero."""
        name = self._clean_name(name)
        if qty <= 0:
            raise ValueError("quantity must be positive")
        if name not in self._items:
            raise KeyError(name)
        if self._items[name] < qty:
            raise ValueError(f"only {self._items[name]} of {name} in stock")
        self._items[name] -= qty
        if self._items[name] == 0:
            del self._items[name]

    def quantity(self, name: str) -> int:
        return self._items.get(self._clean_name(name), 0)

    def items(self) -> list[tuple[str, int]]:
        """(name, quantity) pairs sorted by name."""
        return sorted(self._items.items())
