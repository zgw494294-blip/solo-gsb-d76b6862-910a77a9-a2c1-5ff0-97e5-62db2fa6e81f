class NetworkError(Exception):
    """管网输入不合法或求解未收敛（message 为面向用户的中文说明）。"""

    def __init__(self, message: str, code: str = "invalid_network"):
        super().__init__(message)
        self.message = message
        self.code = code
