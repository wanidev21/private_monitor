"""웹 대시보드 서버 (FastAPI + uvicorn). main.py가 daemon 스레드에서 start_webserver를 호출한다.

보안: 127.0.0.1에만 바인드, Host 헤더가 localhost/127.0.0.1이 아니면 403 (DNS rebinding 방지),
설정 변경 POST는 Content-Type: application/json만 허용 (다른 사이트의 폼 전송 CSRF 차단), CORS 미사용.
"""
import io
import logging
import socket

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates

from monitor import fonts, iconrender, paths, winapi
from monitor.config import OVERLAY_LOOKS

log = logging.getLogger(__name__)

ALLOWED_HOSTS = ("localhost", "127.0.0.1")

_server: uvicorn.Server | None = None


def is_running() -> bool:
    """대시보드 서버가 요청을 받을 수 있는 상태인지. False면 "대시보드 열기"를 비활성화한다."""
    return _server is not None and bool(getattr(_server, "started", False))


def host_allowed(host_header: str) -> bool:
    host = host_header.strip().lower()
    if host.startswith("["):  # IPv6 리터럴은 허용하지 않음 (127.0.0.1에만 바인드)
        return False
    return host.rsplit(":", 1)[0] in ALLOWED_HOSTS


def _require_json(request: Request) -> None:
    """상태를 바꾸는 POST는 JSON만 허용 (다른 사이트의 폼 전송 CSRF 차단)."""
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "application/json":
        raise HTTPException(status_code=415, detail="Content-Type: application/json 이어야 합니다")


def create_app(collector, config, font_names: list[str], tray=None, template_dir: str | None = None,
               notifier=None, sync_auto_start=None) -> FastAPI:
    """sync_auto_start(enabled): auto_start가 바뀌면 레지스트리에 반영하는 함수 (테스트에서는 가짜 주입)."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    templates = Jinja2Templates(directory=template_dir or paths.get_template_dir())

    @app.middleware("http")
    async def check_host(request: Request, call_next):
        if not host_allowed(request.headers.get("host", "")):
            return JSONResponse({"detail": "forbidden host"}, status_code=403)
        return await call_next(request)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return templates.TemplateResponse(request, "dashboard.html", {"initial_config": config.snapshot()})

    @app.get("/api/status")
    def status():
        data = collector.latest
        if data is None:
            raise HTTPException(status_code=503, detail="수집 중")
        return data

    @app.get("/api/history")
    def history():
        return collector.get_history()

    @app.get("/api/config")
    def get_config():
        return config.snapshot()

    @app.post("/api/config")
    async def post_config(request: Request):
        _require_json(request)
        try:
            changes = await request.json()
        except ValueError:
            raise HTTPException(status_code=400, detail="JSON 형식이 아닙니다") from None
        if not isinstance(changes, dict):
            raise HTTPException(status_code=422, detail="설정 변경은 객체여야 합니다")
        try:
            changed = config.update(changes)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from None
        except OSError:
            log.exception("cannot save config")
            raise HTTPException(status_code=500, detail="설정 파일을 저장하지 못했습니다") from None
        if changed and tray is not None:
            tray.wake()  # 트레이 아이콘 즉시 재구성. 오버레이/팝업은 자체 주기로 반영
        if "auto_start" in changes and sync_auto_start is not None:
            try:
                sync_auto_start(config.auto_start)
            except OSError:
                log.exception("cannot sync auto start")
                raise HTTPException(status_code=500, detail="자동 실행 레지스트리를 변경하지 못했습니다") from None
        return {"changed": changed, "config": config.snapshot()}

    @app.get("/api/fonts")
    def get_fonts():
        return font_names

    @app.post("/api/alerts/test")
    def alerts_test(request: Request):
        _require_json(request)
        if notifier is None:
            raise HTTPException(status_code=503, detail="알림 기능이 동작하지 않습니다")
        notifier.send_test()
        return {"sent": True}

    @app.get("/api/overlay-preview")
    def overlay_preview(look: str = "label"):
        if look not in OVERLAY_LOOKS:
            raise HTTPException(status_code=422, detail=f"look은 {', '.join(OVERLAY_LOOKS)} 중 하나")
        snapshot = config.snapshot()
        image = iconrender.render_overlay_preview(
            look, snapshot["tray_visible_items"], collector.latest, collector.get_history(), snapshot["ui_theme"],
            size=snapshot["overlay_icon_size"],
            light_taskbar=winapi.is_light_taskbar(),
            font_file=fonts.resolve_font_file(snapshot["overlay_font"]),
        )
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        return Response(buffer.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})

    return app


def _port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def start_webserver(collector, config, font_names: list[str], tray=None, notifier=None,
                    sync_auto_start=None) -> None:
    """daemon 스레드에서 호출. 블로킹. 실패해도 예외를 밖으로 던지지 않는다 (트레이/팝업은 계속 동작)."""
    global _server
    port = config.web_port
    if not _port_available(port):
        log.error("web port %s in use; dashboard disabled", port)
        return
    try:
        app = create_app(collector, config, font_names, tray, notifier=notifier, sync_auto_start=sync_auto_start)
        # log_config=None: 콘솔 없는 exe에서 uvicorn 기본 로깅이 sys.stdout.isatty()를 호출하다 크래시하는 문제 방지
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                                               log_config=None, access_log=False))
        _server = server
        log.info("dashboard: http://127.0.0.1:%s", port)
        server.run()
    except BaseException:  # uvicorn은 바인드 실패 시 SystemExit를 던진다
        log.exception("webserver stopped")
