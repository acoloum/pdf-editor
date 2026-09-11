class EditorError(Exception):
    """可直接呈現給使用者的錯誤。"""
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code

class BatchPublishError(EditorError):
    def __init__(self, message, completed=()):
        super().__init__("BATCH_FAILED", message)
        self.completed = tuple(completed)

