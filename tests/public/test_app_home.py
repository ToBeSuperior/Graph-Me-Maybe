from __future__ import annotations

from pathlib import Path

from graphme import context_menu
from graphme.config import app_home, load_settings


def test_the_settings_and_the_right_click_menu_use_the_one_app_home(tmp_path: Path) -> None:
    for platform, env in (("darwin", {"HOME": str(tmp_path)}),
                          ("win32", {"LOCALAPPDATA": str(tmp_path / "L")}),
                          ("linux", {"HOME": str(tmp_path)})):
        assert context_menu.default_home(platform, env) == app_home(env, platform)
    env = {"GRAPHME_HOME": str(tmp_path / "h")}
    assert load_settings(environ=env).local_models == app_home(env) == tmp_path / "h"
