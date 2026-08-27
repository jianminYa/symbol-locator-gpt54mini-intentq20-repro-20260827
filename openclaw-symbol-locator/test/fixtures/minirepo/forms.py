"""Form base classes."""


class BaseForm:
    """Base form with save() method."""

    def __init__(self, data: dict) -> None:
        self.data = data

    def save(self, commit: bool = True) -> dict:
        """Save the form data — returns saved payload."""
        if commit:
            return {"saved": self.data}
        return {"draft": self.data}
