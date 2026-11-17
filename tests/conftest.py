"""Root conftest for the test suite.

``pysnapmaker`` is a plain-Python library nested in the integration, whose own
``__init__.py`` imports Home Assistant. Putting the integration folder on
``sys.path`` lets the library tests ``import pysnapmaker`` without Home
Assistant. The repo root is added too, so tests that do need Home Assistant can
``import custom_components.snapmaker_u1``.
"""
import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
INTEGRATION_DIR = os.path.join(REPO_ROOT, "custom_components", "snapmaker_u1")

for path in (REPO_ROOT, INTEGRATION_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)
