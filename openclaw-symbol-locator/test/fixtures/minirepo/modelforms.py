"""Model-backed forms."""

from forms import BaseForm
from models import Model


class ModelForm(BaseForm):
    """Form that materializes a Model on save."""

    def __init__(self, data: dict, model_cls: type[Model]) -> None:
        super().__init__(data)
        self.model_cls = model_cls

    def save(self, commit: bool = True) -> Model | dict:
        """Save via the underlying model when commit=True."""
        if not commit:
            return super().save(commit=False)
        m = self.model_cls(pk=0, payload=self.data)
        m.save()
        return m
