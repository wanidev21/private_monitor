"""PC System Monitor 엔트리포인트."""
import ctypes
import logging
import os
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
import webbrowser

from monitor import startup, webserver
from monitor.collector import Collector
from monitor.config import Config
from monitor.logger import MetricsLogger, setup_app_logging
from monitor.notifier import Notifier
from monitor.overlay import TaskbarOverlay
from monitor.popup import PopupWindow
from monitor.tray import TrayIcon

log = logging.getLogger("main")

_ERROR_ALREADY_EXISTS = 183


def _guard_std_streams() -> None:
    """--windowed exe에서는 stdout/stderr가 None → 라이브러리가 쓰기 시도 시 크래시하므로 막는다."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")


def _acquire_single_instance():
    """이미 실행 중이면 None. 반환된 핸들은 프로세스 종료까지 유지해야 한다."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, False, "Local\\PCMonitor_SingleInstance")
    if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
        return None
    return handle


def _enable_dpi_awareness() -> None:
    """고DPI 모니터에서 흐림/좌표 어긋남 방지. tk.Tk() 생성 전에 호출해야 한다."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
    except Exception:
        log.debug("SetProcessDpiAwareness failed", exc_info=True)


def main() -> None:
    if sys.platform != "win32":
        print("Windows 전용 앱입니다.")
        sys.exit(1)

    _guard_std_streams()
    setup_app_logging()

    mutex = _acquire_single_instance()
    if mutex is None:
        log.info("already running; exit")
        return

    _enable_dpi_awareness()
    mock = "--mock" in sys.argv
    log.info("starting (mock=%s)", mock)

    # 0. 설정 로드
    config = Config()

    # 자동 실행 레지스트리를 설정과 동기화 (미등록/경로 변경 시 등록, 꺼져 있으면 해제)
    try:
        startup.sync_auto_start(config.auto_start)
    except OSError:
        log.exception("cannot sync auto start")

    # 1. tkinter root를 메인 스레드에서 생성 (tkinter는 반드시 메인 스레드)
    root = tk.Tk()
    root.withdraw()
    root.report_callback_exception = lambda *exc: log.error("tkinter callback error", exc_info=exc)

    # 2. 데이터 수집기
    collector = Collector(config=config, mock=mock)
    threading.Thread(target=collector.run, name="collector", daemon=True).start()

    # 폰트 목록은 메인 스레드에서 한 번만 조회해 웹서버에 넘긴다 (웹 스레드에서 tkinter 호출 금지)
    font_names = sorted({f for f in tkfont.families(root) if not f.startswith("@")})

    def open_web():
        if webserver.is_running():
            # localhost는 IPv6(::1)를 먼저 시도해 느려질 수 있다. 서버는 127.0.0.1에만 바인드하므로 IP로 연다
            webbrowser.open(f"http://127.0.0.1:{config.web_port}")
        else:
            log.warning("dashboard is not running")

    # 4. 팝업 윈도우 (tkinter 기반, 메인 스레드에서 관리)
    popup = PopupWindow(root, collector, config, on_open_web=open_web, is_web_available=webserver.is_running)

    # 5. 시스템 트레이 (pystray 스레드 → root.after로 메인 스레드에 위임)
    tray = TrayIcon(
        collector,
        config,
        on_show_popup=lambda: root.after(0, popup.toggle),
        on_open_web=open_web,
        on_quit=lambda: root.after(0, root.quit),
        is_web_available=webserver.is_running,
    )
    threading.Thread(target=tray.run, name="tray", daemon=True).start()

    # 3. 웹 대시보드 서버 (별도 daemon 스레드, 127.0.0.1:web_port). 실패해도 앱은 계속 동작
    # 알림 (daemon 스레드: 새 샘플마다 임계값 판정, 발송은 별도 스레드)
    notifier = Notifier(collector, config)
    threading.Thread(target=notifier.run, name="notifier", daemon=True).start()

    # 수치 CSV 로그 (daemon 스레드: 1분 평균, 날짜별 파일, 7일 보관)
    metrics_logger = MetricsLogger(collector)
    threading.Thread(target=metrics_logger.run, name="metrics-log", daemon=True).start()

    threading.Thread(target=webserver.start_webserver,
                     args=(collector, config, font_names, tray, notifier, startup.sync_auto_start),
                     name="webserver", daemon=True).start()

    # 5-1. 작업표시줄 오버레이 (tkinter, 메인 스레드). overlay 모드가 아니면 아무것도 표시하지 않음
    overlay = TaskbarOverlay(
        root, collector, config, tray,
        on_show_popup=popup.toggle,
        on_open_web=open_web,
        on_quit=root.quit,
        is_web_available=webserver.is_running,
    )
    overlay.start()

    # 6. tkinter 메인루프
    try:
        root.mainloop()
    finally:
        # 7. 종료 정리
        try:
            overlay.destroy()
        except Exception:
            log.debug("overlay destroy failed", exc_info=True)
        tray.stop()
        notifier.stop()
        metrics_logger.stop()
        try:
            metrics_logger.flush()  # 진행 중인 분의 평균도 남긴다
        except Exception:
            log.debug("metrics flush failed", exc_info=True)
        collector.shutdown()
        log.info("stopped")


if __name__ == "__main__":
    main()
