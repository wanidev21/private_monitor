"""설정/로그/리소스 경로. PyInstaller로 빌드(frozen)된 경우와 개발 중 경로를 분기한다."""
import os
import sys

# 테스트 등에서 사용자 홈 대신 다른 폴더를 쓰고 싶을 때 지정
HOME_ENV = "PC_MONITOR_HOME"


def get_app_dir() -> str:
    """설정/로그를 저장하는 사용자 폴더 (%USERPROFILE%/.pc-monitor). 없으면 생성."""
    base = os.path.normpath(os.environ.get(HOME_ENV) or os.path.expanduser("~/.pc-monitor"))
    os.makedirs(base, exist_ok=True)
    return base


def get_config_path() -> str:
    return os.path.join(get_app_dir(), "config.json")


def get_log_dir() -> str:
    path = os.path.join(get_app_dir(), "logs")
    os.makedirs(path, exist_ok=True)
    return path


def get_resource_dir() -> str:
    """templates/, assets/가 들어 있는 폴더."""
    if getattr(sys, "frozen", False):
        return sys._MEIPASS
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def get_template_dir() -> str:
    return os.path.join(get_resource_dir(), "templates")


def get_assets_dir() -> str:
    return os.path.join(get_resource_dir(), "assets")
