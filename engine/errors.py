class EngineError(Exception):
    def __init__(self, category, message, position=None):
        super().__init__(message)
        self.category = category
        self.message = message
        self.position = position
    def __str__(self):
        if self.position is None:
            return f"{self.category}: {self.message}"
        return f"{self.category}: {self.message} at position {self.position}"
