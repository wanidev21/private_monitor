"""설치된 글꼴 이름 → 글꼴 파일 경로 (Windows 레지스트리 기준)."""
import functools
import os
import re
import winreg

_FONTS_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
_SUFFIX_RE = re.compile(r"\s*\((TrueType|OpenType)\)\s*$", re.IGNORECASE)


@functools.lru_cache(maxsize=1)
def installed_fonts() -> dict[str, str]:
    """{소문자 글꼴 이름: 파일 전체 경로}. 시스템 글꼴(HKLM)과 사용자 글꼴(HKCU) 모두.

    레지스트리 값 이름 예: 'Segoe UI Semibold (TrueType)', 'Cambria & Cambria Math (TrueType)'.
    """
    fonts = {}
    fonts_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            key = winreg.OpenKey(root, _FONTS_KEY)
        except OSError:
            continue
        with key:
            index = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, index)
                except OSError:
                    break
                index += 1
                if not isinstance(value, str) or not value:
                    continue
                path = value if os.path.isabs(value) else os.path.join(fonts_dir, value)
                for part in _SUFFIX_RE.sub("", name).split("&"):
                    fonts.setdefault(part.strip().lower(), path)
    return fonts


def resolve_font_file(name: str | None) -> str | None:
    """글꼴 이름에 해당하는 파일 경로. 설치되어 있지 않으면 None."""
    if not name:
        return None
    path = installed_fonts().get(name.strip().lower())
    return path if path and os.path.exists(path) else None
