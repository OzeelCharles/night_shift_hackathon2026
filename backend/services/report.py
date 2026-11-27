"""Messages produits par un cas d'usage, que le contrôleur affichera (flash)."""


class Report(list):
    """Liste de (message, catégorie) ; les messages vides sont ignorés."""

    def add(self, message: str | None, category: str = "info") -> "Report":
        if message:
            self.append((message, category))
        return self

    def info(self, *messages) -> "Report":
        for m in messages:
            self.add(m, "info")
        return self

    def success(self, message) -> "Report":
        return self.add(message, "success")

    def warning(self, message) -> "Report":
        return self.add(message, "warning")

    def error(self, message) -> "Report":
        return self.add(message, "error")

    def __add__(self, other) -> "Report":
        combined = Report(self)
        combined.extend(other)
        return combined
