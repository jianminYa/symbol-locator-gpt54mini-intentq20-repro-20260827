"""Model layer."""


class Model:
    """Database model with save() method."""

    def __init__(self, pk: int, payload: dict) -> None:
        self.pk = pk
        self.payload = payload

    def save(self) -> None:
        """Persist this row to storage."""
        # pretend to write to db
        print(f"model#{self.pk} saved")
