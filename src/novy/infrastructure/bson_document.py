from typing import Any


class StrictDocument(dict[str, Any]):
    def __setitem__(self, key: str, value: Any) -> None:
        if key in self:
            raise ValueError("Duplicate BSON key")
        super().__setitem__(key, value)
