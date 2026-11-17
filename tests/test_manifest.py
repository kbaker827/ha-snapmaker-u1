"""Checks on manifest.json and hacs.json.

The camera offers an RTSP stream (Extended Firmware) through Home Assistant's
``stream`` component. Without ``stream`` as a dependency, installs that don't
use ``default_config:`` fail with "does not support play stream service".
"""
import json
import os

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
MANIFEST_PATH = os.path.join(REPO_ROOT, "custom_components", "snapmaker_u1", "manifest.json")
HACS_PATH = os.path.join(REPO_ROOT, "hacs.json")


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as file:
        return json.load(file)


def test_manifest_declares_stream_dependency_for_rtsp_camera():
    assert "stream" in _load(MANIFEST_PATH)["dependencies"]


def test_manifest_dependencies_are_sorted_and_unique():
    dependencies = _load(MANIFEST_PATH)["dependencies"]

    assert dependencies == sorted(set(dependencies))


def test_manifest_has_no_keys_hassfest_rejects():
    # hassfest's custom integration schema has no "homeassistant" key; the
    # minimum Home Assistant version belongs in hacs.json.
    assert "homeassistant" not in _load(MANIFEST_PATH)


def test_manifest_keys_are_in_hassfest_order():
    keys = list(_load(MANIFEST_PATH))

    assert keys[:2] == ["domain", "name"]
    assert keys[2:] == sorted(keys[2:])


def test_manifest_links_issue_tracker_for_hacs():
    assert _load(MANIFEST_PATH)["issue_tracker"].endswith("/issues")


def test_hacs_declares_minimum_home_assistant_version():
    assert _load(HACS_PATH)["homeassistant"]


def test_manifest_does_not_list_home_assistant_core_requirements():
    # hassfest rejects requirements Home Assistant already ships with.
    requirements = _load(MANIFEST_PATH)["requirements"]

    assert not [r for r in requirements if r.split(">")[0].split("=")[0] == "aiohttp"]
