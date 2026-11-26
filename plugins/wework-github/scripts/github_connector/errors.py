"""Public, credential-free errors shared by the connector entry points."""


class GitHubError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message
