"""TouchDesigner CLI runtime."""

# A constant, not importlib.metadata: reading package metadata costs every td start ~130 ms.
# tests/test_release_cli.py keeps it equal to the version in pyproject.toml.
__version__ = "0.11.0"
