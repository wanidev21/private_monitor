# PC Monitor — Windows 11 작업표시줄 시스템 모니터

> **이 문서만 읽고 현재 구현을 처음부터 다시 만들 수 있도록** 최종 사양, 핵심 알고리즘, 수치, 사용자 결정, 폐기한 방식(과 이유)을 모두 정리한다.
> 코드와 문서가 다르면 **코드가 기준**이며, 문서를 즉시 고친다.
> 상태: 10단계 전부 구현·검증 완료 (테스트 136개 통과, exe 빌드·실행 확인). 남은 사용자 확인: PC 재부팅 후 자동 실행, exe에서 알림/클릭 동작.

---

## 1. 제품 개요

Windows 11 작업표시줄에 CPU · RAM · GPU · 디스크 · 네트워크 수치를 실시간으로 보여주는 상주 앱.

| 기능 | 요약 |
|---|---|
| **작업표시줄 오버레이** | 모니터마다 작업표시줄 오른쪽에 수치 표시. 주 모니터는 `^`(트레이 영역) 왼쪽, 보조 모니터는 시계 왼쪽. 디자인 6종 |
| **상세 팝업** | 오버레이 좌클릭. 수치 + 최근 60초 미니 그래프 + 코어별 막대 + "대시보드 열기" |
| **우클릭 메뉴** | 대시보드 열기 / 오버레이 디자인(6종) / 알림 / Windows 시작 시 자동 실행 / 종료 |
| **웹 대시보드** | `http://127.0.0.1:18099`. 전체 현황, 표시 항목 선택, 디자인·글꼴·색상·알림·자동 실행 설정, 테스트 알림 |
| **알림** | CPU(지속) / RAM / GPU 온도 / 디스크 사용률 초과 시 Windows 토스트 (항목별 5분에 한 번) |
| **CSV 로그** | 1분 평균, 날짜별 파일, 7일 보관 |
| **자동 실행** | HKCU Run 레지스트리 |
| **대체 트레이 아이콘** | 오버레이를 어느 모니터에도 표시할 수 없을 때만 원형 아이콘 1개 |

**핵심 원칙:** 비침투적(작업 방해 금지, 포커스 뺏지 않음), 가볍게, 실용적(장식 최소), 안정적(수집 항목 하나가 실패해도 앱은 계속 동작).

**사용자 결정 요약 (반드시 유지):**
- 트레이 아이콘은 표시 수단으로 쓰지 않는다. **오버레이만** 쓴다 (트레이 16px은 글자가 작고 `^` 오른쪽에만 놓임).
- 오버레이 디자인 6종 모두 제공, 기본은 **라벨형**. 글꼴 기본 **Segoe UI Semibold**. 크기 기본 **32px**.
- **주/보조 모니터 모두 동일하게** 표시 (주·보조를 바꿔도 불편하지 않도록).
- 오버레이는 **직접 클릭**(좌클릭 팝업, 우클릭 메뉴)한다. 클릭 통과 방식 아님.
- CPU 온도·클럭은 수집하지 않는다. 더블클릭은 쓰지 않는다.

---

## 2. 환경과 기술 스택

- **Windows 11 전용** (`sys.platform != "win32"`면 안내 후 종료).
- **Python: python.org 설치판 3.14** (`C:\Python314\python.exe`, 3.11+ 호환 문법). 가상환경 `.venv` 필수.
  - **Microsoft Store판 Python 금지** (`py -3.13`이 Store판을 가리켰음): 샌드박스라 HKCU Run 쓰기가 가상화되고 PyInstaller 문제.
  - 이 PC의 PATH `python`은 3.10(패키지 없음)이므로 항상 `.venv\Scripts\python.exe`를 쓴다.
- 기본 venv의 `python.exe`는 기본 인터프리터(`C:\Python314\python.exe`)를 **자식 프로세스로** 띄운다 → 실행 파일 경로가 필요하면 `QueryFullProcessImageNameW` 사용.

**requirements.txt**
```
psutil>=5.9.0
nvidia-ml-py>=12.535.0
pystray>=0.19.5
Pillow>=10.0.0
winotify>=1.1.0
fastapi>=0.110.0
uvicorn>=0.29.0
jinja2>=3.1.0
pyinstaller>=6.10.0
```
**requirements-dev.txt**: `-r requirements.txt` + `httpx>=0.27.0` (FastAPI TestClient).

| 라이브러리 | 용도 | 주의 |
|---|---|---|
| psutil | CPU/RAM/디스크/네트워크 | Windows에 온도 API 없음, `cpu_freq()`는 고정값 |
| nvidia-ml-py (import `pynvml`) | GPU 사용률/온도/VRAM | PyPI `pynvml` 패키지는 deprecated 래퍼 → 설치 금지. GPUtil 금지. `nvml.dll`은 드라이버(System32)에 포함 |
| tkinter | 오버레이 창, 팝업, 툴팁, 우클릭 메뉴 | 반드시 메인 스레드 |
| Pillow | 오버레이/아이콘 렌더링, 화면 샘플링 `ImageGrab` | Pillow 12: `getdata()` deprecated → `getchannel()`/`tobytes()` |
| pystray | 대체 트레이 아이콘 | 0.19.5의 uID 버그 → 고정 GUID로 우회 (§6.10) |
| winotify | 토스트 알림 | PowerShell `-Command`, `STARTF_USESHOWWINDOW`(숨김)로 실행 → 콘솔 깜빡임 없음 |
| FastAPI + uvicorn + Jinja2 | 웹 대시보드 | exe에서 `log_config=None` 필수 |
| PyInstaller | exe (onedir) | |

설치 버전(검증 당시): Python 3.14.5, PyInstaller 6.22.3, fastapi 0.141, uvicorn 0.53, pillow 12.3, psutil 7.2, nvidia-ml-py 13.610, pystray 0.19.5, winotify 1.1.0, httpx 0.28.

---

## 3. 프로젝트 구조

```
C:\waniwani\monitor\
├── CLAUDE.md
├── README.md               # 사용자용: 실행/빌드/파일 위치/문제 해결
├── main.py                 # 엔트리포인트
├── build.bat               # exe 빌드 (CRLF 줄바꿈 필수)
├── requirements.txt / requirements-dev.txt
├── assets/
│   ├── make_icon.py        # icon.ico 생성
│   └── icon.ico
├── templates/
│   └── dashboard.html      # 단일 HTML (CSS/JS 인라인, 외부 CDN 없음)
├── monitor/
│   ├── __init__.py
│   ├── paths.py            # 설정/로그/리소스 경로
│   ├── config.py           # 설정 로드/검증/원자적 저장
│   ├── collector.py        # 수집 (psutil, pynvml)
│   ├── display.py          # 문자열/색상 계산 (순수 함수)
│   ├── graph.py            # 히스토리 시리즈 계산 + tk 미니 그래프
│   ├── fonts.py            # 글꼴 이름 → 파일 (레지스트리)
│   ├── iconrender.py       # 오버레이 디자인 6종 + 원 아이콘 렌더링
│   ├── winapi.py           # ctypes: 작업표시줄/모니터/전체화면/창 스타일/드라이브 매핑/테마
│   ├── overlay.py          # 작업표시줄 오버레이 (모니터별 tk 창)
│   ├── popup.py            # 상세 팝업 (tk)
│   ├── tray.py             # 대체 트레이 아이콘 (pystray)
│   ├── notifyicon.py       # 고정 GUID 트레이 아이콘 + Win11 자동 표시
│   ├── notifier.py         # 알림 판정 + 토스트
│   ├── logger.py           # app.log + CSV 수치 로그
│   ├── startup.py          # 자동 실행 레지스트리
│   └── webserver.py        # FastAPI 대시보드 서버
├── tests/                  # unittest (16개 파일)
└── (빌드 산출물) build/, dist/PCMonitor/, PCMonitor.spec
```

**의존 방향:** `display`(순수) ← `graph` ← `iconrender` ← `overlay`/`webserver`. `winapi`는 독립. UI 모듈은 `collector.latest`, `collector.get_history()`, `config`만 읽는다.

**사용자 데이터 위치**
| 내용 | 경로 |
|---|---|
| 설정 | `%USERPROFILE%\.pc-monitor\config.json` (환경변수 `PC_MONITOR_HOME`로 변경 가능 — 테스트용) |
| CSV | `%USERPROFILE%\.pc-monitor\logs\YYYY-MM-DD.csv` |
| 앱 로그 | `%USERPROFILE%\.pc-monitor\logs\app.log` |
| 자동 실행 | `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 값 `PCMonitor` |
| 토스트 발신자 | `HKCU\Software\Classes\AppUserModelId\PCMonitor.SystemMonitor` (`DisplayName=PC Monitor`) |
| 대체 트레이 아이콘 표시 설정 | `HKCU\Control Panel\NotifyIconSettings\<id>` (Windows가 관리) |

---

## 4. 아키텍처: main.py 흐름과 스레드

```
main():
  win32 아님 → print 후 exit(1)
  _guard_std_streams()        # sys.stdout/stderr가 None(--windowed exe)이면 os.devnull로 대체
  setup_app_logging()
  _acquire_single_instance()  # CreateMutexW("Local\\PCMonitor_SingleInstance"), GetLastError==183이면 조용히 return
                              #   kernel32 = WinDLL("kernel32", use_last_error=True), restype c_void_p, 핸들은 프로세스 끝까지 유지
  SetProcessDpiAwareness(2)   # PER_MONITOR_AWARE, 반드시 tk.Tk() 전
  mock = "--mock" in sys.argv
  config = Config()
  startup.sync_auto_start(config.auto_start)   # OSError는 로그
  root = tk.Tk(); root.withdraw(); root.report_callback_exception = 로그
  collector = Collector(config, mock);            Thread(collector.run, name="collector", daemon)
  font_names = sorted({f for f in tkfont.families(root) if not f.startswith("@")})   # 메인 스레드에서 1회
  open_web(): webserver.is_running()이면 webbrowser.open("http://127.0.0.1:{web_port}") 아니면 경고 로그
  popup = PopupWindow(root, collector, config, on_open_web=open_web, is_web_available=webserver.is_running)
  tray = TrayIcon(collector, config, on_show_popup=lambda: root.after(0, popup.toggle),
                  on_open_web=open_web, on_quit=lambda: root.after(0, root.quit), is_web_available=...)
                                                  Thread(tray.run, name="tray", daemon)
  notifier = Notifier(collector, config);         Thread(notifier.run, name="notifier", daemon)
  metrics_logger = MetricsLogger(collector);      Thread(metrics_logger.run, name="metrics-log", daemon)
  Thread(webserver.start_webserver, args=(collector, config, font_names, tray, notifier, startup.sync_auto_start),
         name="webserver", daemon)
  overlay = TaskbarOverlay(root, collector, config, tray, on_show_popup=popup.toggle,
                           on_open_web=open_web, on_quit=root.quit, is_web_available=...)
  overlay.start()
  try: root.mainloop()
  finally: overlay.destroy() → tray.stop() → notifier.stop() → metrics_logger.stop() + flush() → collector.shutdown()
```

**스레드 규칙**
- tkinter(오버레이, 팝업, 툴팁, 메뉴)는 **메인 스레드에서만**. 다른 스레드는 `root.after(0, fn)`로 위임.
- pystray 콜백은 pystray 스레드에서 실행 → tkinter를 직접 건드리지 않는다.
- collector는 샘플 dict를 새로 만들어 `_latest`에 **통째로 교체**(만든 뒤 절대 수정하지 않음) → 읽기에 Lock 불필요. 히스토리 deque는 Lock.
- `localhost`는 IPv6(::1)를 먼저 시도해 느려질 수 있다 → 브라우저는 `127.0.0.1`로 연다.

---

## 5. 데이터 계약

### 5.1 수집 샘플 (`collector.latest`)
```python
{
    "cpu": {"percent": 45.2, "per_core": [32.0, 55.1, ...]},              # 실패 시 None
    "ram": {"used_gb": 8.2, "total_gb": 16.0, "percent": 51.3},          # 실패 시 None
    "gpu": {"name": "NVIDIA GeForce RTX 2060", "percent": 23, "temp": 62,
            "vram_used_gb": 2.1, "vram_total_gb": 6.0},                  # 드라이버 없음/실패 시 None
    "disks": [{"mount": "C:\\", "used_gb": 120.0, "total_gb": 256.0, "percent": 46.9,
               "read_mb_s": 15.21, "write_mb_s": 3.1}],                   # 실패 시 []. I/O 매핑 실패 시 read/write None
    "network": {"upload_kb_s": 125.3, "download_kb_s": 2048.7,
                "today_upload_mb": 85.0, "today_download_mb": 1228.8},   # 실패 시 None
    "timestamp": 1700000000.0                                            # time.time()
}
```
반올림: cpu/ram/disk 사용률 1자리, GB 1자리, 디스크 MB/s 2자리, 네트워크 KB/s·오늘 MB 1자리.

### 5.2 표시 항목 키 (`config.tray_visible_items`, 이름은 호환 위해 유지)
`"cpu"`, `"ram"`, `"gpu"`(사용률), `"gpu_temp"`, `"net_down"`, `"net_up"`, `"disk:C:\\"`(드라이브 문자 대문자). **배열 순서 = 오버레이 배치 순서.** 기본값 `["cpu","ram","gpu","gpu_temp","net_down","net_up"]`.

---

## 6. 모듈별 명세

### 6.1 paths.py
- `HOME_ENV = "PC_MONITOR_HOME"`.
- `get_app_dir()`: `normpath(env or ~/.pc-monitor)` 생성 후 반환. `get_config_path()`, `get_log_dir()`(생성).
- `get_resource_dir()`: frozen이면 `sys._MEIPASS`, 아니면 프로젝트 루트. `get_template_dir()`, `get_assets_dir()`.

### 6.2 config.py

**DEFAULTS**
```json
{
    "web_port": 18099,
    "update_interval_sec": 2,
    "alerts_enabled": true,
    "alert_thresholds": {"cpu_percent": 90, "cpu_duration_sec": 10, "ram_percent": 90, "gpu_temp": 85, "disk_percent": 95},
    "auto_start": true,
    "tray_auto_promote": true,
    "overlay_icon_look": "label",
    "overlay_icon_size": 32,
    "overlay_font": "Segoe UI Semibold",
    "tray_visible_items": ["cpu", "ram", "gpu", "gpu_temp", "net_down", "net_up"],
    "ui_theme": {
        "bg": "#1E1E1E", "fg": "#E0E0E0", "fg_dim": "#888888",
        "accent_cpu": "#4FC3F7", "accent_ram": "#81C784", "accent_gpu": "#FFB74D",
        "accent_net_up": "#EF5350", "accent_net_down": "#42A5F5", "accent_disk": "#CE93D8",
        "graph_bg": "#2A2A2A", "section_gap": 12, "font_family": "Segoe UI",
        "font_size_title": 11, "font_size_value": 10, "font_size_small": 9
    }
}
```
`OVERLAY_LOOKS = ("label", "badge", "gauge", "ring", "spark", "text")`

**SCHEMA (검증 규칙)**
| 키 | 규칙 |
|---|---|
| web_port | 정수 1024~65535 |
| update_interval_sec | 정수 1~10 |
| alerts_enabled, auto_start, tray_auto_promote | bool (정수 불가) |
| alert_thresholds.cpu_percent / ram_percent / disk_percent | 정수 1~100 |
| alert_thresholds.cpu_duration_sec | 정수 1~600 |
| alert_thresholds.gpu_temp | 정수 30~110 |
| overlay_icon_look | OVERLAY_LOOKS 중 하나 |
| overlay_icon_size | 정수 24~40 |
| overlay_font, ui_theme.font_family | 공백 제거 후 1~100자 문자열 |
| tray_visible_items | 리스트. `"network"` → `net_down`,`net_up`으로 치환, `disk:x:\`는 대문자 정규화, 중복·알 수 없는 항목 제거(경고 로그), 문자열 아닌 항목은 오류 |
| ui_theme 색 10종 | `#RRGGBB` → 대문자로 정규화 |
| ui_theme.section_gap | 정수 0~50 |
| ui_theme.font_size_* | 정수 6~32 |

정수 규칙: `bool`이면 거부, `float`라도 정수값이면 허용 후 int 변환.

**`_normalize(schema, defaults, data, strict)`**: 스키마의 키만 결과에 넣는다 → **알 수 없는 키는 버림**(제거된 `tray_icon_style`도 자동 삭제). 없는 키는 기본값. strict=True(웹 변경)면 `"키.경로: 이유"` ValueError, strict=False(파일 로드)면 경고 후 기본값.

**Config 클래스**
- `RLock`, `_version`(변경 성공 시 +1), `_data`.
- `__getattr__(name)`: `_`로 시작하거나 DEFAULTS에 없으면 AttributeError, 있으면 **deepcopy** 반환.
- `snapshot()`: deepcopy. `set(key, value)` = `update({key: value})`.
- `update(changes)`: dict 아니면 ValueError → `_deep_merge`(dict만 재귀 병합, 리스트는 통째 교체) → strict normalize → 같으면 False → `_write` 성공 후 메모리 교체·version+1 → True. 실패 시 메모리/파일 모두 그대로.
- `_load()`: 파일 없음 → 기본값 저장. 읽기 OSError → 기본값(저장 안 함). JSON 오류 또는 최상위가 dict 아님 → `config.json.bak`으로 이동 후 기본값 저장. 정상 → 비엄격 normalize, 결과가 원본과 다르면 저장.
- `_write()`: 같은 폴더에 `mkstemp(prefix=".config-", suffix=".tmp")` → `json.dump(ensure_ascii=False, indent=4)` → flush+fsync → `os.replace`(PermissionError 시 50ms 간격 5회 재시도) → 실패하면 임시 파일 삭제.
- `python -m monitor.config`: 경로와 설정 JSON 출력.

### 6.3 collector.py

**Collector(config, mock=False)**: `DRIVE_REFRESH_SEC=60`, `HISTORY_WINDOW_SEC=60`, `DEFAULT_INTERVAL_SEC=2`, `deque(maxlen=600)`.
- 시간 함수를 속성으로 둔다(테스트 교체용): `clock=time.monotonic`, `wall_clock=time.time`, `today=date.today`.
- mock이 아니면: `init_gpu()`, 그리고 `psutil.cpu_percent(None)`, `cpu_percent(None, percpu=True)`를 한 번 호출해 기준점 확보(첫 호출은 항상 0).
- `latest` 프로퍼티, `get_history(window=60)`: timestamp ≥ now-window인 샘플 목록, `collect_once()`: mock이면 `get_mock_data()`, 히스토리 추가 + latest 교체.
- `run()`: 루프마다 수집 → `interval = config.update_interval_sec`(예외 시 2) → `stop_event.wait(max(0.1, interval - 경과))`.
- `shutdown()`: stop + `nvmlShutdown()`.
- `_section(name, fn)`: 예외 시 None, 같은 항목 오류 로그는 복구될 때까지 1회, 복구 시 info 로그.
- 경과 시간 `elapsed` = monotonic 차이(첫 샘플 None).

**GPU**: `init_gpu(log_failure=True)` = `nvmlInit()` → `nvmlDeviceGetHandleByIndex(0)`(실패 None). `read_gpu_info(handle)`: `GetUtilizationRates().gpu`, `GetTemperature(NVML_TEMPERATURE_GPU)`, `GetMemoryInfo` used/total GB, `GetName`(bytes면 decode). 실패 시 예외.
- **재연결 `_collect_gpu()`** (`GPU_RETRY_SEC=30`): 읽기 실패 → 경고 1회 + `nvmlShutdown()` + 핸들 None. 핸들이 None이면(시작 시 실패 포함) 30초마다 `init_gpu(log_failure=False)` 재시도, 복구되면 info `GPU recovered`.
- 이유: 앱 실행 중 드라이버 업데이트(595.79 → 617.42)로 옛 핸들이 무효가 되어 재시작 전까지 GPU가 계속 None이었다(실패 로그도 debug라 안 남았음).

**디스크**
- 60초마다 `disk_partitions(all=False)` 재조회. `fstype` 빈 문자열이거나 `opts`에 `cdrom`이면 제외. 각 드라이브에 `winapi.get_physical_drive_name(letter)` 매핑 캐시.
- 매 샘플 `disk_io_counters(perdisk=True)`(예외 시 {}), 드라이브별 `disk_usage(mount)`(예외 시 그 드라이브 건너뜀).
- 매핑된 물리 디스크가 카운터에 있으면: 이전 샘플도 있고 elapsed 있으면 `max(0, 차이)/elapsed/MB`, 아니면 0.0. 매핑 없으면 None. 한 물리 디스크의 파티션들은 같은 I/O 값을 공유.

**네트워크**: `net_io_counters()` 차이(음수면 0)/elapsed/KB. 로컬 날짜가 바뀌면 오늘 누적 0으로 초기화 후 차이를 누적.

**mock**: CPU 10~95%(12코어), RAM 16GB, RTX 2060, 디스크 C/D 2개, 네트워크 임의값, 오늘 누적 고정(85.0/1228.8).

**CLI** `python -m monitor.collector [--mock]`: 실제 수집이면 1회 수집 → 1초 대기 → 다시 수집해 JSON 출력.

### 6.4 display.py (순수 함수, UI 무관)

**색상**: `GREEN #4CAF50`(≤50), `ORANGE #FF9800`(≤80), `RED #F44336`(>80), `GRAY #9E9E9E`(값 없음). `level_color(v)`. GPU 온도는 °C 값을 그대로 넣는다(62 → 주황). `alert_thresholds`와 무관.

**서식**
- `format_rate(kb, compact)`: <1024 → `"125 KB/s"`/`"125K"`, <1024MB → `"2.0 MB/s"`/`"2.0M"`, 그 외 GB `"1.2 GB/s"`/`"1.2G"`.
- `format_size_gb(16.0)="16"`, `(31.8)="31.8"`. `format_amount_mb(mb)`: <1024 `"85 MB"` else `"1.2 GB"`. `format_disk_rate(None)="-"`, <10 `"1.0MB/s"`, 이상 `"15MB/s"`.
- `disk_letter(mount)`, `find_disk(data, mount)`(대소문자 무시).

**오버레이 항목 모양 `icon_face(item, data, theme, prev_net_unit) → (IconFace(label, text, color), net_unit)`**
| 항목 | 라벨 | 숫자 | 색 |
|---|---|---|---|
| cpu / ram / gpu / disk:C:\ | `CPU` / `RAM` / `GPU` / `C:` | `str(min(int(round(v)), 99))` | level_color(v) |
| gpu_temp | `G°C` | 온도 정수(최대 99) | level_color(온도) |
| net_down / net_up | `↓`/`↑` + 단위(`KB`/`MB`/`GB`) | 아래 규칙 | `theme.accent_net_down` / `accent_net_up` |
| 값 없음 | 같은 라벨 (네트워크는 `↓`+이전 단위 또는 KB) | `--` | GRAY |

**네트워크 단위** (1KB=1024B): 자연 단위 = <100KB → KB, <100MB(=102400KB) → MB, 그 외 GB.
- `choose_net_unit(kb, prev)`: 자연 단위가 이전보다 크거나 같으면 즉시 전환. 작으면 **이전 단위 하한(MB=100KB, GB=102400KB)의 90% 이상이면 이전 단위 유지**(히스테리시스), 미만이면 자연 단위.
- `format_net_icon_value(kb, unit)`: KB → 정수(최대 99). MB/GB → 값<1이면 `"." + max(1, int(값*10))`(`.1`~`.9`, 버림), 아니면 정수(최대 99).

**`OVERLAY_LOOK_LABELS`** (config.OVERLAY_LOOKS와 순서 일치 필수): label 라벨형, badge 배지형, gauge 게이지형, ring 원형 링, spark 미니 그래프형, text 두 줄 텍스트.

**두 줄 텍스트 `text_design_parts(item, data) → (label, value, color|None)`**: 라벨 `CPU/RAM/GPU/G°C/↓/↑/C:`. 네트워크 값 `format_rate(compact=True)`, 온도 `"62°"`, 그 외 `"45%"`, 없음 `"--"`. 비율/온도가 80 초과면 level_color, 아니면 None(기본 글자색).

**오버레이 툴팁 `icon_tooltip(item, data)`** (127자 제한, data None이면 `"PC Monitor (수집 중...)"`)
- `CPU 사용률 45%` / `RAM 사용률 87% (13.9 / 16.0 GB)` / `GPU 사용률 23% (VRAM 2.1 / 6.0 GB)` / `GPU 온도 62°C`
- `다운로드 2.0 MB/s (오늘 누적 1.2 GB)` / `업로드 125 KB/s (오늘 누적 85 MB)`
- `C: 드라이브 58% (120 / 256 GB)  R 1.0MB/s  W 0.0MB/s` / 분리 시 `C: 드라이브 연결 안 됨` / 정보 없음 `CPU 정보 없음` 등

**대체 트레이 툴팁 `build_circle_tooltip(data, items)`**: 항목 없으면 `"PC Monitor (좌클릭: 상세 / 우클릭: 메뉴)"`, data 없으면 `"PC Monitor (수집 중...)"`. 부분 문자열: `CPU: 45%`, `RAM: 8.2/16GB (51%)`(축약 `RAM: 51%`), GPU는 gpu/gpu_temp를 묶어 `GPU: 23% 62°C`, 네트워크는 묶어 `↓2.0M ↑125K`, 디스크 `Disk C: 58%`(축약 `C:58%`, 분리된 드라이브는 생략). 한 줄에 두 개씩 `" | "`로 잇고 줄바꿈. 127자 초과 시 축약형, 그래도 넘으면 127자에서 자름.

**팝업 텍스트**
- `popup_sections(items, data)`: 순서 cpu → ram → gpu(gpu 또는 gpu_temp) → 디스크(선택 순서, data가 있으면 분리된 드라이브 제외) → net(net_down 또는 net_up).
- `popup_title`: `CPU`/`RAM`/`GPU`/`Network`/`C:`. `popup_has_subline`: ram, gpu, net, 디스크.
- `popup_texts(section, data) → (값, 보조 줄, 진행%)`: data None → `("--","",None)`.
  - cpu `"45.2%"` / ram `("51%", "8.2 / 16.0 GB")` / gpu `("62°C   23%", "VRAM 2.1 / 6.0 GB")` / 없으면 `"N/A"`
  - net `("↓ 2.0 MB/s\n↑ 125 KB/s", "오늘 ↓ 2 MB   ↑ 1 MB")` (두 줄 — 한 줄이면 그래프가 밀려남)
  - 디스크 `("120 / 256 GB (58%)", "R 1.0MB/s   W 0.0MB/s", 58.0)`, 분리 `("연결 안 됨", "", None)`

### 6.5 graph.py
- `WINDOW_SEC=60`, `FILL_RATIO=0.3`, `NET_MIN_SCALE_KB=64`, `NET_SCALE_HEADROOM=1.15`.
- `blend(color, background, ratio)`: `#RRGGBB` 혼합(대문자).
- `sample_value(sample, key)`: net_down/up → KB/s, gpu_temp → temp, `disk:` → percent, 그 외 section percent. 없으면 None. `series_from_history(history, key) → [(ts, v)]`.
- `auto_scale(values) = max(max(values, default=0) * 1.15, 64)`.
- `to_segments(series, now, width, height, vmax)`: `age = now - ts`, age>60 또는 < -1이면 제외. 값 None이면 선분 끊기. `x = w-1 - max(0,age)/60*(w-1)`, `y = (h-1) - min(max(v,0)/vmax, 1)*(h-1)`.
- `MiniGraph(parent, w, h, bg, lines=[(key,color)], auto, label_color, label_font)`: `draw(history, now)` — auto면 vmax=auto_scale(모든 선 값), 아니면 100. 모든 채우기(선 색과 배경 30:70 혼합 폴리곤)를 먼저, 그다음 선(폭 1), 점 하나면 2x2 사각형. auto면 좌상단 `(3,1)`에 `format_rate(vmax)`.

### 6.6 fonts.py
- `installed_fonts()`(lru_cache): `HKLM`과 `HKCU`의 `SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts` 값을 읽어 `{소문자 이름: 전체 경로}`. 값 이름에서 `" (TrueType)"`/`" (OpenType)"` 제거, `&`로 나눠 각각 등록, 상대 경로면 `%WINDIR%\Fonts`. 먼저 등록된 것이 우선.
- `resolve_font_file(name)`: 공백 제거·소문자로 찾고 파일이 있으면 경로, 아니면 None. (예: `Segoe UI Semibold` → `seguisb.ttf`, 사용자 글꼴 `DX영화자막 M` → `%LOCALAPPDATA%\Microsoft\Windows\Fonts\DX영화자막.ttf`)

### 6.7 iconrender.py — 오버레이 디자인 렌더링

**공통 상수**: `OVERLAY_BASE=32`(모든 치수는 크기/32배), `DEFAULT_OVERLAY_FONT_FILE="seguisb.ttf"`, 슈퍼샘플 4배, 트랙색 라이트 `(200,200,200)` / 다크 `(70,70,70)`, 글자색 라이트 (라벨 `(90,90,90)`, 숫자 `(20,20,20)`) / 다크 (라벨 `(180,180,180)`, 숫자 `(240,240,240)`), 배지 글자 흰색.

**글꼴 도우미**
- `_truetype(file, px)`(lru_cache): file → `seguisb.ttf` → `ImageFont.load_default(size=px)` 순으로 시도.
- `_fit_truetype(file, px, text, max_width)`: 폭(`_text_length`)이 맞을 때까지 1px씩 줄임(최소 6).
- `_center_text(draw, text, font, fill, left, right, top, bottom)`: textbbox 기준 가로·세로 가운데.
- `_draw_arrow(draw, x, top, bottom, up, fill, s)`: `↑↓`는 **글꼴 글자가 아니라 도형**(머리 삼각형 높이 `max(2, round(3s))`, 반폭 2.5s, 중심 x+2s, 막대 반폭 `max(0.5, 0.5s)`).
- `_draw_label(draw, label, file, px, fill, box, s, align="center"|"left")`: 라벨 첫 글자가 `↓/↑`면 화살표 폭 `round(6s)` + 나머지 글자.

**`°` 뒤 글자 당기기 (사용자 지적: `G° C`처럼 벌어짐)**
- `DEGREE_MIN_GAP_RATIO = 0.08`.
- `_ink_range(font, text)`: 빈 L 이미지(폭 `getlength+size*3`, 높이 `size*3`)의 `(size, size//2)`에 그린 뒤 `getbbox()`로 **실제 잉크 x 구간**. (`font.getbbox()`는 글꼴에 따라 잉크가 아니라 글자 칸 전체를 돌려줌 — DX영화자막 `°`는 잉크 3~5px인데 bbox 9px)
- `_degree_kern_px(font, next_char)`(캐시 키: 글꼴 경로·크기·뒤 글자): `gap = getlength("°") + ink(next).left - ink("°").right`, `kern = max(0, gap - 0.08*size)`.
- `_text_length(text, font) = getlength(text) - Σ kern(°, 뒤 글자)`, `_draw_text(draw, xy, text, font, fill, anchor)`: `°`로 나눠 조각마다 같은 anchor로 그리고, 뒤 조각이 있으면 x를 kern만큼 당김. 가운데 정렬·폭 맞춤·두 줄 텍스트 모두 이 두 함수를 쓴다.
- 결과: Segoe UI Semibold는 0(변화 없음), DX영화자막은 실제로 붙음.

**디자인별 항목 이미지 (크기 s = size/32)**

| 디자인 | 기본 크기(32 기준) | 그리기 |
|---|---|---|
| label | 정사각 px | 하단 막대 높이 `max(1,round(2s))`, 가로 `round(2s)`~`px-1-round(2s)`, 색=face.color. 라벨 띠 `0~round(11s)` 글꼴 `round(12s)`, 숫자 띠 `round(11s)~px-막대-1` 글꼴 `round(20s)`(폭 px-2에 맞춤) |
| badge | 정사각 px | 둥근 사각형(반경 `max(2,round(4s))`, 색=face.color), 흰 라벨 띠 `round(1s)~round(12s)`, 숫자 띠 `round(12s)~px-1` |
| gauge | 34×40 | k=h/40. 4배로 트랙색 둥근 사각형(반경 `max(2,round(5k))`) 위에 아래에서 비율만큼 face.color 알파 150 → LANCZOS 축소. 라벨 띠 `round(3k)~round(14k)` 글꼴 `round(10k)`, 숫자 띠 `round(14k)~h-round(2k)` 글꼴 `round(17k)` |
| ring | 34×44 | k=h/44, 지름 `min(w, round(32k))`. 4배로 inset `round(2k·4)`, 선 두께 `max(4, round(3k·4))` 트랙 원 + `-90°`부터 `360·max(비율,0.01)` 호(face.color) → 축소. 숫자는 원 안 가운데 글꼴 `round(13k)`(폭 지름-round(8k)), 라벨은 원 아래 띠 글꼴 `round(9k)`, 화살표 배율 k·0.8 |
| spark | 56×40 | k=h/40. 트랙색 배경, 위 `round(15k)`는 글자 영역, 아래가 그래프(`to_segments`, 채우기 = face.color와 트랙색 35:65 혼합, 선 1px) → 반경 `max(2,round(4k))` 둥근 알파 마스크. 라벨 왼쪽 위(여백 `round(4k)`, 폭 0.55w, 글꼴 `round(10k)`, 왼쪽 정렬), 숫자 오른쪽 위(글꼴 `round(13k)`, 폭 0.45w-여백) |
| text | 전체 한 장 | 아래 참조 |

- **비율/vmax `_scale_info(item, data, history)`**: 네트워크는 히스토리 값+현재값의 `auto_scale`로 vmax를 정하고 비율=현재/vmax. 그 외는 `item_value/100`(0~1), vmax 100. 값 없음 0.
- **두 줄 텍스트 `_render_text_strip`**: 라벨 글꼴 `max(7, round(11k))`, 값 글꼴 `max(8, round(15k))`, 라벨-값 간격 `round(5k)`, 열 간격 `round(16k)`, 좌우 여백 `max(2, round(4k))`. 항목을 두 개씩 묶어 열로 배치(1열: 1·2번째 …). **열 폭은 가장 긴 값 샘플로 고정**(네트워크 `"1023K"`, 온도 `"100°"`, 그 외 `"100%"`) → 값이 바뀌어도 폭 불변. 행 높이=작업표시줄/2, 중심 y는 행 중앙에서 위 행 +10%, 아래 행 -10%(가운데로 모음), anchor `"lm"`. 값 색은 `text_design_parts`의 색 또는 기본 숫자색. hitbox는 (항목, 열 x, 행 y0, 열 x+폭, 행 y1).

**`render_overlay_strip(look, items, data, history, theme, size, bar_height, light, font_file, net_units, now) → (OverlayStrip(image, hitboxes), net_units)`**
- `size`는 DPI 배율이 적용된 크기. k=size/32.
- text면 위 함수. 그 외 항목마다 `icon_face(…, net_units[item])`로 단위 상태 갱신.
  - gauge/ring/spark: `scale = min(k, (bar_height-4)/기본높이)`, 크기 `max(8, round(기본×scale))` → 작업표시줄보다 커지지 않는다.
  - label/badge: `px = max(12, min(round(size), bar_height-4))`.
- 가로 배치: 간격 `max(4, round(8k))`, 좌우 여백 `max(2, round(4k))`, 각 이미지는 세로 가운데. 결과는 **투명 배경 RGBA, 높이 = 작업표시줄 높이**, hitbox `(항목, x0, y0, x1, y1)`.

**기타**
- `render_circle(color, size=64)`: 대체 트레이 아이콘(여백 size//16 원).
- `render_overlay_preview(look, items, data, history, theme, size=32, light, font_file, bar_height=48)`: 배경 라이트 `(238,238,238)` / 다크 `(32,32,32)`에 strip을 좌우 8px 여백으로 올린 이미지. 항목 없으면 48×48에 회색 원 16px.
- `python -m monitor.iconrender [--out PNG] [--size N]`: 6종 × 라이트/다크 미리보기(고정 샘플 + 난수 시드 7 히스토리).

### 6.8 winapi.py

**작업표시줄 찾기**
- `TaskbarGeometry(hwnd, bar, notify, tasks, monitor, dpi, hidden)` — 사각형은 `(l, t, r, b)` 물리 픽셀.
- 주: `FindWindowW("Shell_TrayWnd")`, 트레이 영역 `FindWindowExW(tray, "TrayNotifyWnd")`(없으면 None 반환), 앱 버튼 `ReBarWindow32`.
- 보조: `FindWindowExW(None, prev, "Shell_SecondaryTrayWnd")`로 모두 순회, `notify=None`, 앱 버튼은 **`WorkerW`**(보조에는 ReBar가 없음 — 이 PC에서 확인). 보조 작업표시줄에는 트레이 영역이 없고 오른쪽 끝에 시계(XAML, 창 핸들 없음)만 있다(방해 금지·알림이 있으면 그 오른쪽에 알림 종 아이콘).
- `hidden` = `not IsWindowVisible` 또는 모니터 안에 보이는 높이가 작업표시줄 높이의 절반 미만(자동 숨김). `dpi = GetDpiForWindow(hwnd) or 96`.
- `find_taskbars()`: 주(있으면) + 보조들.

**오버레이 창 제어**
- `get_root_hwnd(winfo_id)` = `GetAncestor(GA_ROOT)`.
- `add_ex_style(hwnd, flags)`: `GetWindowLongPtrW/SetWindowLongPtrW(GWL_EXSTYLE)` (restype/argtypes `c_ssize_t` 필수).
- `set_window_owner(hwnd, owner)`: `SetWindowLongPtrW(hwnd, GWLP_HWNDPARENT=-8, owner)`.
- `place_topmost(hwnd, x, y, w, h)`: `SetWindowPos(HWND_TOPMOST, SWP_NOACTIVATE|SWP_SHOWWINDOW)`.

**전체화면에 가려진 작업표시줄 `covered_taskbars(taskbars) → set(hwnd)`**
```python
ZWindow = namedtuple("ZWindow", "hwnd rect eligible")

def zorder_windows():   # GetTopWindow(None) → GetWindow(GW_HWNDNEXT) 순서 = Z-order 위→아래
    eligible = (IsWindowVisible and not IsIconic
                and class not in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd")
                and pid != os.getpid()
                and not ex_style & (WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW)   # 클릭 통과 드라이버 오버레이 등
                and not DwmGetWindowAttribute(DWMWA_CLOAKED=14))

def find_covered_taskbars(windows, monitors):   # monitors = {작업표시줄 hwnd: 모니터 사각형}, 순수 함수
    covered, pending, covering = set(), dict(monitors), []
    for w in windows:
        if w.hwnd in pending:
            monitor = pending.pop(w.hwnd)
            if any(rect_covers(r, monitor) for r in covering): covered.add(w.hwnd)
            if not pending: break
        elif w.eligible and w.rect: covering.append(w.rect)
    return covered
```
작업표시줄보다 **Z-order 위에 있으면서 그 모니터 전체를 덮는** 적격 창이 있으면 가려진 것. 최대화 창은 작업표시줄을 덮지 않으므로 해당 없음.

**팝업 위치/포커스**
- `get_cursor_monitor() → CursorMonitor(x, y, work, scale)`: `GetCursorPos` → `MonitorFromPoint(NEAREST)` → `GetMonitorInfoW.rcWork`, 배율 `shcore.GetDpiForMonitor(hmon, 0)/96`(argtypes 지정 필수, 실패 시 1.0).
- `foreground_window() → (hwnd, pid)`.

**기타**
- `is_light_taskbar()`: `HKCU\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize\SystemUsesLightTheme` (없으면 False=다크).
- `get_physical_drive_name(letter)`:
```python
h = CreateFileW(rf"\\.\{letter}:", 0, FILE_SHARE_READ|FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None)  # access=0: 관리자 불필요
DeviceIoControl(h, IOCTL_STORAGE_GET_DEVICE_NUMBER=0x2D1080, None, 0, byref(STORAGE_DEVICE_NUMBER), ...)
return f"PhysicalDrive{sdn.DeviceNumber}"   # 실패 시 None, finally CloseHandle
```
  (CreateFileW restype=HANDLE, INVALID = `HANDLE(-1).value`. 스토리지 공간/동적 디스크는 실패)
- 남아 있지만 **사용하지 않는** 함수: `is_fullscreen_app_running()`(SHQueryUserNotificationState), `foreground_fullscreen_monitor()` — 전체화면 판정에 쓰면 안 된다(§9).

### 6.9 overlay.py — 작업표시줄 오버레이

**상수**: `TICK_MS=1000`, `FULLSCREEN_POLL_MS=200`, `KEY_RESAMPLE_SEC=60`, `CLOCK_RESCAN_SEC=30`, `PRIMARY_MARGIN=8`, `SECONDARY_MARGIN=12`, `CLOCK_FALLBACK_WIDTH=150`, `CLOCK_SEARCH_WIDTH=220`, `CLOCK_DIFF_THRESHOLD=40`, `CLOCK_GAP_TOLERANCE=20`. 툴팁 배경 `#F9F9F9`, 글자 `#1A1A1A`, 테두리 `#C8C8C8`, 글꼴 Segoe UI 9. (px 값은 100% 기준, 작업표시줄 DPI 배율을 곱해 사용)

**배치 `compute_placement(anchor_right, bar, tasks, width, margin)`**: width≤0이면 None. `x = anchor_right - margin - width`, 왼쪽 한계 = `(tasks.right 또는 bar.left) + margin`, x가 한계보다 작으면 None. 반환 `(x, bar.top, width, bar 높이)` — 기준 왼쪽에 오른쪽 정렬, 세로는 작업표시줄 전체.

**기준점 `_anchor`**: 주는 `notify.left`, 간격 8. 보조는 시계 왼쪽 끝, 간격 12. 시계 위치는 처음 / 작업표시줄 사각형이 바뀔 때 / 30초마다 다시 찾는다.

**시계 찾기 `find_clock_left(strip, search_width, ignore)`** (작업표시줄 전체 캡처 RGB):
- 배경색 = (오른쪽 끝 2열 전체 + 탐색 구간의 y=1 행) 픽셀 중 최빈값.
- 열 x가 내용 = y 4~h-5 중 `|R-bR|+|G-bG|+|B-bB| ≥ 40`인 픽셀이 있음 (ignore 구간은 제외).
- 오른쪽 끝부터 왼쪽으로 search_width까지 훑어, 내용을 만나면 left=x, 틈 카운터 0. 내용이 시작된 뒤 **틈이 20px 이상 이어지면 중단**. 반환 left(없으면 None).
- 20px인 이유: 시계 글자 사이 틈뿐 아니라 **시계 오른쪽 알림 종 아이콘과의 약 14px 틈**까지 한 덩어리로 봐야 한다 (6px일 때 종만 시계로 잡혀 오버레이가 시계를 덮음).
- 이미 표시 중인 자기 오버레이 구간을 `ignore`로 제외 (간격 12 < 틈 허용치 20이므로 필수).
- 못 찾으면 `bar.right - round(150·배율)`.
- 캡처: `ImageGrab.grab(bbox=bar, all_screens=True)` — bbox는 실제 화면 좌표(음수 가능).

**`_OverlayWindow`** (작업표시줄 hwnd당 1개)
- 생성: `tk.Toplevel` → withdraw → `overrideredirect(True)` → Canvas(`highlightthickness=0, bd=0`, 이미지 아이템 1개 `anchor="nw"`) → 이벤트 `<Button-1>` 팝업, `<Button-3>` 메뉴, `<Motion>` 툴팁, `<Leave>` 툴팁 숨김 → `update_idletasks` → hwnd = `get_root_hwnd(winfo_id)` → `add_ex_style(WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)` (**`WS_EX_TRANSPARENT` 금지** — 클릭을 받아야 함).
- `update(geometry, look, items, size, font_file, light, data, history, theme) → bool`:
  1. 배율 = dpi/96, 기준점·간격 계산.
  2. `render_overlay_strip(…, round(size·배율), bar_height, …, self.net_units)`.
  3. `compute_placement`가 None이면 hide 후 False.
  4. 투명 키 색 샘플링: `(anchor - max(1, margin//2), bar.top + 3)` 한 픽셀을 ImageGrab (실패 시 라이트 `(238,238,238)`/다크 `(32,32,32)`). 처음, 테마 변경 시, 60초마다. 색이 바뀌면 `win.configure(bg)`, `win.attributes("-transparentcolor", key)`, `canvas.configure(bg)`.
  5. 그리기: `(크기, 키색, image.tobytes())`가 이전과 같으면 생략. 아니면 키 색 불투명 배경 위에 strip을 `alpha_composite` → RGB `ImageTk.PhotoImage(master=win)`(참조 보관 필수) → 캔버스 이미지 교체. (반투명 가장자리를 미리 작업표시줄 색과 섞어 키 색 투명화 시 테두리 번짐 방지)
  6. hitbox·bar_top 저장. 작업표시줄 hwnd가 바뀌었으면 `set_window_owner`.
  7. 처음 표시 또는 위치 변경 시 `canvas.configure(width,height)` → `deiconify` → `place_topmost`. True.
- `hide()`: withdraw, visible False, placement None. `destroy()`.

**`TaskbarOverlay` 관리자**
- `start()`: `after(0, _tick)`, `after(200, _fullscreen_tick)`.
- `_tick()` (1초): `_update()`, 예외 시 로그 + 전부 숨김 + 대체 아이콘 True.
- `_update()`:
  1. items = `config.tray_visible_items`. 항목이 있으면 `find_taskbars()`. 항목은 있는데 작업표시줄이 없으면 경고 로그 1회.
  2. `self._taskbars` 저장. 목록에 없는 작업표시줄의 창은 파괴(모니터 분리·탐색기 재시작), 그 창의 툴팁이면 숨김.
  3. 항목 없음 또는 작업표시줄 없음 → 전부 숨김, **대체 아이콘 True**, 끝.
  4. `covered = covered_taskbars(taskbars)`, look/size/`resolve_font_file(overlay_font)`/`is_light_taskbar()`/latest/`get_history(60)`/ui_theme.
  5. 작업표시줄마다 창을 만들거나 가져와서: `hidden` 또는 covered면 hide + suppressed. 아니면 `update()` 결과로 visible.
  6. **대체 아이콘 = (보이는 창 없음) and (자동 숨김·전체화면으로 숨긴 것도 없음)** — 전체화면/자동 숨김 때문에 트레이 아이콘을 띄우지 않는다. 값이 바뀔 때만 `tray.set_overlay_fallback`.
  7. 툴팁이 떠 있으면 내용 갱신.
- `_fullscreen_tick()` (200ms): 보이는 창이 있을 때만 `covered_taskbars(self._taskbars)`, 가려진 창 즉시 hide(툴팁도). 다시 보이는 판단은 `_tick`이 한다. (1초 주기만으로는 유튜브 전체화면 전환 직후 오버레이가 잠깐 보였음)
- 마우스: 좌클릭 → `on_show_popup`. `<Motion>` → hitbox 안 항목을 찾아 툴팁, 없으면 숨김.
- 툴팁: 한 개의 `Toplevel`(overrideredirect, topmost, 1px 테두리 Label padx 8 pady 4). 텍스트 `icon_tooltip`. x = 항목 중심 - 폭/2를 그 모니터 좌우 안으로 제한, y = `bar_top - 높이 - 6`.
- 우클릭 메뉴(`tk.Menu(tearoff=0)`, 한 번 만들고 재사용): `대시보드 열기`(index 0, 서버 없으면 disabled) / `오버레이 디자인` ▶ 6종 radiobutton(StringVar) / `알림` checkbutton / `Windows 시작 시 자동 실행` checkbutton(`startup.apply_auto_start`) / 구분선 / `종료`. 열 때마다 변수들을 config 값으로 맞추고 `tk_popup(x_root, y_root)` → finally `grab_release()`. 디자인 변경은 `config.set` 후 `after(0, _update)`.

### 6.10 tray.py + notifyicon.py — 대체 트레이 아이콘

**tray.py**
- `desired_layout(overlay_fallback)`: True → `("circle", ("main",))`, False → `("none", ())`.
- `TrayIcon.run()`(daemon): 루프마다 레이아웃이 바뀌면 `_rebuild`, `refresh()`, `wake_event.wait(update_interval_sec)`. 끝나면 아이콘 제거.
- `set_overlay_fallback(v)`, `wake()`: 값 설정 + wake_event.
- `_create_icon()`: `GuidIcon(icon_guid("main"), "PCMonitor", icon=render_circle(CPU 부하색), title=build_circle_tooltip, menu, on_added)` → `Thread(icon.run, kwargs={"setup": icon.visible=True})` → 추가 이벤트 3초 대기. `on_added`에서 `tray_auto_promote`면 `start_auto_promote(guid)`.
- `_remove()`(Lock 보유 상태): `icon.stop()`, 스레드 join 2초.
- `refresh()`: 색 구간이 바뀔 때만 이미지 교체, 툴팁이 바뀌면 교체.
- 메뉴: `상세 보기`(`default=True, visible=False` → **좌클릭 시 실행**) / `대시보드 열기`(enabled=서버 동작) / `오버레이 디자인` ▶ radio 6종 / `알림`(checked) / `Windows 시작 시 자동 실행`(checked, `apply_auto_start(config, not auto_start)`) / 구분선 / `종료`. 메뉴 콜백은 pystray 스레드 → 설정만 바꾸고 관리 스레드를 깨운다(콜백 안에서 자기 아이콘 stop 금지).

**notifyicon.py (필수 우회)**
- pystray 0.19.5는 `NOTIFYICONDATAW`에 `hID=id(self)`를 넘기는데 구조체 필드명이 `uID`라서 **모든 아이콘이 uID=0으로 등록**된다.
- `GuidIcon(pystray._win32.Icon)`: `__init__(guid, *args, on_added=None, **kwargs)`에서 속성을 **super().__init__ 전에** 설정.
```python
def _message(self, code, flags, **kwargs):
    guid = win32.NOTIFYICONDATAW.GUID.from_buffer_copy(self._guid.bytes_le)
    if code == win32.NIM_ADD:
        self._shell(win32.NIM_DELETE, win32.NIF_GUID, guid)   # 비정상 종료로 남은 같은 GUID 제거 (없으면 NIM_ADD 실패)
    ok = self._shell(code, flags | win32.NIF_GUID, guid, **kwargs)
    if code == win32.NIM_ADD: 실패면 경고, 성공이면 on_added(guid)
def _shell(self, code, flags, guid, **kwargs):
    return bool(win32.Shell_NotifyIcon(code, win32.NOTIFYICONDATAW(
        cbSize=ctypes.sizeof(win32.NOTIFYICONDATAW), hWnd=self._hwnd, uFlags=flags, guidItem=guid, **kwargs)))
```
- `icon_guid(item, exe_path=None) = uuid5(UUID("7d3c1f5e-2b9a-4c61-9f0e-5a8b6c4d2e10"), f"{실행파일경로.lower()}|{item}")`. **네임스페이스 변경 금지.** Windows는 GUID 아이콘을 실행 파일 경로에 묶으므로 경로가 다르면 GUID도 달라야 한다.
- `process_image_path()`: `QueryFullProcessImageNameW(GetCurrentProcess())` (restype/argtypes 지정 — HANDLE 오버플로 방지).
- **Win11 자동 표시**: `HKCU\Control Panel\NotifyIconSettings\<id>`의 `IconGuid`(`{GUID 대문자}`)가 일치하는 키를 찾아 **`IsPromoted` 값이 없을 때만** DWORD 1 설정("promoted"), 있으면 "already_set"(사용자가 직접 끈 아이콘 존중), 키 없음 "not_found". 탐색기가 키를 만드는 데 수 초 → daemon 스레드에서 1초 간격 최대 30초 재시도. 공식 API 아님 → 실패는 로그만.

### 6.11 popup.py — 상세 팝업

**상수**: `BASE_WIDTH=400`, 그래프 200×40, `MARGIN=10`, `TOGGLE_DEBOUNCE_SEC=0.3`, `FOCUS_CHECK_DELAY_MS=100`, `FOREGROUND_POLL_MS=200`.

**위치 `calc_popup_position(cursor, w, h)`**: 커서 x가 작업 영역 가운데 이상이면 `right-w-10` 아니면 `left+10`, y도 같은 방식(아래 → `bottom-h-10`). 커서가 있는 모니터의 `rcWork` 기준이라 다중 모니터·음수 좌표에서 동작. (`winfo_screenwidth`, `SHAppBarMessage` 금지)

**동작**
- `toggle()`: 보이면 hide. 숨긴 지 0.3초 안이면 무시(오버레이 클릭 → 포커스 잃고 닫힘 → 같은 클릭으로 다시 열리는 경합 방지). 아니면 show.
- `show()`: 커서 모니터·배율 → 창 준비 → **`_refresh()`로 값을 먼저 채움**(빈 라벨로 높이를 재면 하단 버튼이 잘렸음) → `_place()`(폭 `round(400·배율)`, 높이 `min(내용 요청 높이+2, 작업 영역 높이-20)`) → deiconify, lift, topmost, `focus_force()`(overrideredirect 창은 자동 포커스를 못 받음) → 전경 창 기록, 갱신 예약, 전경 확인 예약.
- `hide()`: 예약 취소, withdraw, 숨긴 시각 기록.
- 창: `Toplevel`, withdraw, overrideredirect, topmost, `<FocusOut>` → 100ms 뒤 `focus_get()`이 None이거나 팝업 자식이 아니면(`str(focused).startswith(str(win))`, KeyError도) hide. `<Escape>` hide.
- **보조 닫기**: 200ms마다 전경 창 pid가 자기 프로세스면 had_focus=True, 아니면 (had_focus이거나 전경 창이 표시 시점과 다르면) hide.
- 갱신: `update_interval_sec`마다 `_refresh`. 재구성 키 `(config.version, 섹션들, round(배율,2), 코어 수)`가 바뀌면 위젯을 다시 만들고, 보이는 중이면 값 채운 뒤 재배치.

**레이아웃** (배율 s, theme 값 사용 — 하드코딩 금지)
- 창 배경 `graph_bg`(1px 테두리 효과) 안에 내용 Frame(bg, padx `round(14s)`, pady `round(12s)`, padx/pady 1).
- 글꼴: 제목 `(font_family, font_size_title, "bold")`, 값 `(…, font_size_value)`, **큰 값 `(…, font_size_value+4, "bold")`**, 작게 `(…, font_size_small)`. 섹션 간격 `round(section_gap·s)`.
- 섹션 없으면 `"표시할 항목이 없습니다.\n대시보드에서 트레이 표시 항목을 선택하세요."`
- **CPU/RAM/GPU/Network**: 위 줄 = 왼쪽(제목 accent 색 + 큰 값, 여러 줄은 왼쪽 정렬) + 오른쪽 `MiniGraph(round(200s)×round(40s), graph_bg, auto=네트워크만, 라벨 글꼴 (family,7))`. accent: cpu `accent_cpu`, ram `accent_ram`, gpu `accent_gpu`, net 선 2개 `accent_net_down`/`accent_net_up`(제목은 down 색). 보조 줄(ram/gpu/net) 작게·dim.
- **CPU 코어 막대**: `core_grid(n)` = 8개 초과 4열, 2~8개 2열, 1개 1열. Canvas 높이 `행×round(4s) + (행-1)×round(3s)`, 막대 높이 `round(4s)`, 행 간격 `round(3s)`, 열 간격 `round(8s)`, 트랙 graph_bg, 채움 accent(80% 초과면 부하색). **`<Configure>` 때 다시 그림**(배치 전 폭 1px).
- **디스크**: 제목(accent_disk)/값 한 줄 + 막대 Canvas 높이 `round(6s)`(80% 초과면 부하색, `<Configure>` 재그림) + 보조 줄.
- 하단 버튼 `대시보드 열기`: bg graph_bg, fg, activebackground accent_cpu, disabledforeground fg_dim, flat, 서버 없으면 disabled. 누르면 hide 후 `on_open_web`.
- 큰 값은 짧게: RAM은 `51%`만 크게(용량은 보조 줄), 네트워크는 두 줄 — 길면 오른쪽 그래프가 창 밖으로 밀림.

### 6.12 webserver.py

- `create_app(collector, config, font_names, tray=None, template_dir=None, notifier=None, sync_auto_start=None)`: `FastAPI(docs_url=None, redoc_url=None, openapi_url=None)`, Jinja2Templates.
- **미들웨어**: `Host` 헤더 소문자, `[`로 시작(IPv6)이면 거부, `rsplit(":",1)[0]`이 `localhost`/`127.0.0.1`이 아니면 403 `{"detail":"forbidden host"}` (DNS rebinding 방지). CORS 미들웨어 없음.
- `_require_json(request)`: Content-Type(세미콜론 앞)이 `application/json`이 아니면 415 (폼 전송 CSRF 차단).

| 메서드 | 경로 | 동작 |
|---|---|---|
| GET | `/` | `TemplateResponse(request, "dashboard.html", {"initial_config": config.snapshot()})` |
| GET | `/api/status` | latest, 없으면 503 `"수집 중"` |
| GET | `/api/history` | `get_history()` |
| GET | `/api/config` | snapshot |
| POST | `/api/config` | JSON 필수(415) → 파싱 실패 400 → dict 아니면 422 → `config.update` ValueError는 422(메시지 그대로), OSError 500 → 바뀌었으면 `tray.wake()` → **본문에 `auto_start` 키가 있으면 `sync_auto_start(config.auto_start)`**(OSError 500) → `{"changed", "config"}` |
| GET | `/api/fonts` | font_names |
| POST | `/api/alerts/test` | JSON 필수 → notifier 없으면 503 → `send_test()` → `{"sent": true}` |
| GET | `/api/overlay-preview?look=` | OVERLAY_LOOKS 아니면 422 → `render_overlay_preview(look, items, latest, history, theme, size=overlay_icon_size, light=is_light_taskbar(), font_file=resolve(overlay_font))` PNG, `Cache-Control: no-store` |

- `is_running()`: 모듈 전역 `_server`가 있고 `server.started`.
- `start_webserver(collector, config, font_names, tray, notifier, sync_auto_start)`: 127.0.0.1:port bind 테스트 실패 → 에러 로그 후 return(앱은 계속). `uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port, log_level="warning", log_config=None, access_log=False))` → `_server` 저장 → 로그 `dashboard: http://127.0.0.1:{port}` → `server.run()`. `except BaseException`(바인드 실패 SystemExit 포함) 로그.
  - `log_config=None`: 콘솔 없는 exe에서 uvicorn 기본 로깅이 `sys.stdout.isatty()` 호출로 크래시하는 문제 방지.

### 6.13 templates/dashboard.html

- 단일 파일, vanilla JS, 외부 리소스 없음. 서버 문자열은 `textContent`/DOM으로만 넣는다. `<script>const INITIAL_CONFIG = {{ initial_config | tojson }};</script>`.
- **CSS 변수**: `--bg --fg --fg-dim --graph-bg --card --accent-cpu --accent-ram --accent-gpu --accent-disk --accent-net-down --accent-net-up --font`. `applyTheme(theme)`가 설정, `--card = mix(bg, graph_bg, 0.6)`, `--font = "<family>", "Segoe UI", sans-serif`(따옴표·역슬래시 제거).
- **헤더**(sticky): `PC System Monitor` + 상태 문구(`연결 중…`/`실시간`/`수집 중…`/빨간 `연결 끊김 — 재시도 중`/`설정 변경 실패: …` 4초) + `⚙ Settings` 버튼.
- **카드 그리드** `repeat(auto-fit, minmax(440px, 1fr))`, 그래프 canvas 240×72(모바일 160):
  - CPU: 큰 `45.2%`, 막대, 그래프, 코어 막대 4열 그리드. 체크박스 `tray`(data-item=cpu)
  - RAM: 큰 `51%`, `8.2 / 16.0 GB`, 막대, 그래프. `tray`
  - GPU: 큰 `62°C · 23%`, 이름, `VRAM …`, VRAM 비율 막대, 사용률 그래프. `사용률 tray`, `온도 tray`
  - Network: 큰 두 줄 `↓ …`/`↑ …`(22px), `오늘 ↓ … ↑ …`, 2색 자동 스케일 그래프(좌상단 스케일). `↓ 다운로드 tray`, `↑ 업로드 tray`
  - Disks: 행 그리드 `44px 1fr 150px 56px 170px 64px`(드라이브, 막대, 용량, 사용률, R/W, tray). 마운트 목록이 바뀔 때만 행을 다시 만든다. 720px 이하 4열.
  - 막대 색: 80% 초과 부하색, 아니면 accent. 전환 애니메이션 0.4s.
- **tray 체크박스**(이름은 tray지만 오버레이 표시 항목): change 시 목록 끝에 추가/제거 → 즉시 POST, 실패 시 되돌리고 상태 문구. 포커스 없는 체크박스만 config로 동기화.
- **Settings 패널**(hidden 토글, 그리드 `repeat(auto-fit, minmax(min(460px,100%),1fr))`, `.settings-section{min-width:0}`)
  - **오버레이 디자인**(선택 즉시): 6개 옵션 2열 그리드 `repeat(2, minmax(0,1fr))`(넓은 미리보기가 열을 늘려 옆 섹션을 덮은 문제 방지), 옵션마다 라디오·제목·설명·`/api/overlay-preview?look=…&t=` 이미지(`max-width:100%`), 선택 옵션 테두리 accent_cpu. 패널이 열려 있는 동안 3초마다 미리보기 새로고침. 오버레이 크기 range 24~40(`change` 때 POST, `input` 때 표시값), 오버레이 글꼴 select.
  - **시작**(선택 즉시): `Windows 시작 시 자동 실행` → POST `auto_start`.
  - **화면**(저장 버튼): 글꼴 select, 글자 크기 3개, 섹션 간격, 갱신 주기 range 1~10(`N초`), 색상 10개 color input(배경, 글자, 보조 글자, 그래프 배경, CPU, RAM, GPU, 디스크, 다운로드, 업로드).
  - **알림**(저장 버튼): 알림 사용 체크 + `테스트 알림 보내기` 버튼(POST `/api/alerts/test` 본문 `{}`), 임계값 5개(CPU 사용률, CPU 지속 시간, RAM 사용률, GPU 온도, 디스크 사용률).
  - `data-dirty` 입력이 바뀌면 formDirty=true, 저장 전 테마 미리 적용. `저장` → `{ui_theme, update_interval_sec, alerts_enabled, alert_thresholds}` POST → `저장했습니다`/`변경 사항이 없습니다`/오류 메시지(빨강). `되돌리기` → 폼·테마 복원.
  - 글꼴 목록이 설정값을 포함하지 않으면 맨 앞에 옵션 추가(`ensureOption`).
- **실시간 갱신**: `setTimeout` 체인으로 `update_interval_sec`마다 `/api/status`(no-store). timestamp가 바뀐 경우만 히스토리(최근 65초 유지)에 추가하고 다시 그림. 503이면 `수집 중…`. 5초마다 `/api/config` 재조회(다른 곳에서 바뀐 값 반영, 편집 중 폼은 유지). 시작 시 `/api/fonts`, `/api/history`로 초기화. 창 크기 변경 시 그래프 다시 그림.
- **그래프**(canvas, devicePixelRatio 반영): X=최근 60초, Y=0~100(네트워크는 max×1.15, 최소 64), 채우기 알파 0.3 먼저, 선 1.5px, 네트워크 좌상단 스케일(10px).

### 6.14 notifier.py

- `APP_ID="PCMonitor.SystemMonitor"`, `APP_DISPLAY_NAME="PC Monitor"`, `COOLDOWN_SEC=300`, `CHECK_INTERVAL_SEC=1.0`.
- **`AlertEvaluator.evaluate(data, thresholds, now=data.timestamp) → [Alert(key, title, message)]`** — 모두 **초과(>)**:
  - CPU: 초과가 처음 관측된 샘플 시각을 기억, `now - 시작 ≥ cpu_duration_sec`이면 후보. 초과가 아니면(또는 cpu None) 시작 시각 초기화.
  - RAM/GPU 온도(gpu None이면 건너뜀)/디스크(드라이브별 키 `disk:C:\`) 즉시.
  - 후보마다 같은 key의 마지막 발송 후 300초 미만이면 제외, 아니면 발송 시각 기록.
  - 문구:
    - `CPU 사용률 높음` / `CPU 사용률이 10초 넘게 90%를 넘었습니다 (현재 97%)`
    - `메모리 사용률 높음` / `RAM 사용률 92% (29.3 / 31.8 GB)`
    - `GPU 온도 높음` / `GPU 온도 88°C (기준 85°C, 사용률 97%)`
    - `D: 드라이브 공간 부족` / `사용률 99.0%, 남은 공간 10.0 GB (기준 95%)` (남은 공간 = max(0, total-used))
  - `reset()`: CPU 시작 시각 초기화.
- `ensure_app_id_registered()`: `HKCU\Software\Classes\AppUserModelId\PCMonitor.SystemMonitor`에 `DisplayName=PC Monitor` (등록 안 된 AppUserModelID의 토스트는 Win11에서 표시되지 않을 수 있음).
- `send_toast(title, msg)`: `winotify.Notification(app_id=APP_ID, title, msg, duration="short").show()`.
- **`Notifier(collector, config, sender=send_toast, async_send=True)`**: `run()` = 등록 후 1초마다 `check()`. `check()`: latest 없거나 같은 timestamp면 무시 → `alerts_enabled`가 false면 `reset()` 후 끝 → 판정 결과마다 `send`. `send()`: info 로그, async면 daemon 스레드에서 `_send_safely`(예외 로그). `send_test()`: `"PC Monitor 테스트 알림"` / `"알림이 정상적으로 표시됩니다. (GPU 60°C 같은 기호도 확인)"`.
- 방해 금지 모드에서는 Windows가 화면에 띄우지 않고 알림 센터에만 쌓는다.

### 6.15 logger.py

**`setup_app_logging(level=INFO)`**: 루트 로거에 `RotatingFileHandler(logs/app.log, 1MB, backupCount=3, utf-8)` + StreamHandler, 형식 `"%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s"`. `sys.excepthook`, `threading.excepthook`(SystemExit 무시)을 `uncaught` 로거 critical로.

**`MetricsLogger(collector, log_dir=None, clock=time.time)`** — CSV 수치 로그
- `BASE_COLUMNS = ["timestamp","cpu_pct","ram_pct","gpu_pct","gpu_temp","net_down_kb","net_up_kb"]`, 디스크 열 `disk_<문자>_pct`. `RETENTION_DAYS=7`, `MAX_PENDING_ROWS=1440`, `POLL_SEC=1`.
- `run()`: `cleanup()` 후 1초마다 `poll()`(새 timestamp만 `add_sample`).
- `add_sample(data)`(Lock): `minute = int(ts // 60)`, 이전 분과 다르면 `_close_minute()`. 값이 None이 아닌 열만 합계/개수 누적, 관측된 디스크 열 기록. 끝에 `_write_pending()`.
- `_close_minute()`: 분 시작 로컬 시각 `"%Y-%m-%d %H:%M"`, 열별 평균, `(날짜, 관측 디스크 열, 행)`을 pending에 추가하고 누적 초기화.
- `flush()`: 진행 중인 분이 있으면 부분 평균으로 닫고 기록 (종료 시 main이 호출).
- `_write_pending()`: 순서대로 `_append_row`. OSError(Excel로 열어 잠김 등)면 경고 1회, **실패한 줄부터 전부 보관**하고 중단(순서 보장), 성공하면 경고 플래그 해제. 최대 1440줄. 마지막에 오늘 날짜가 정리한 날짜와 다르면 `cleanup(today)`.
- `_append_row(날짜, 디스크, 행)`: 파일이 있고 첫 줄 헤더가 있으면 **그 헤더를 따라** 추가(같은 날 재시작 — 새 드라이브 무시, 없는 값 빈칸), 없으면 헤더 `BASE_COLUMNS + sorted(디스크 열)` 작성 후 행. utf-8, BOM 없음, `newline=""`. 값: None→"", 문자열 그대로, 숫자 `"{:.1f}"`.
- `cleanup(today)`: 이름이 `YYYY-MM-DD.csv`인 파일만, `(today - 파일날짜).days >= 7`이면 삭제(오늘 포함 7일 보관). app.log 등은 건드리지 않음.

### 6.16 startup.py

- `APP_NAME="PCMonitor"`, `RUN_KEY=r"Software\Microsoft\Windows\CurrentVersion\Run"`.
- `get_launch_command()`: frozen → `"<exe 경로>"`. 개발 → `"<sys.executable 폴더>\pythonw.exe" "<프로젝트>\main.py"`(pythonw 없으면 sys.executable). 경로는 따옴표로 감싼다.
- `get_registered_command(run_key)`, `is_auto_start_enabled`, `set_auto_start(enable, command, run_key)`(켜기 `CreateKeyEx`+`SetValueEx REG_SZ`, 끄기 `DeleteValue`, 없으면 무시).
- `sync_auto_start(enabled, command=None, run_key)`: 켜짐+미등록 → "registered", 켜짐+명령 다름(대소문자·공백 무시 비교) → "updated", 꺼짐+등록됨 → "removed", 그 외 "unchanged". 바뀌면 info 로그. **`PCMonitor` 값만** 다룬다.
- `apply_auto_start(config, enabled)`: `config.set("auto_start")` + `sync_auto_start`, 실패 로그.
- 호출: 앱 시작 직후, 우클릭 메뉴(오버레이·트레이), 대시보드 POST. exe로 실행하면 다음 동기화 때 exe 경로로 교체된다.

---

## 7. 빌드와 배포

**assets/make_icon.py**: 1024px 캔버스에 둥근 사각형(반경 SIZE/5, `(30,30,30)`), 막대 3개(좌우 여백 20%, 간격 7%, 위아래 20%~80%, 반경 막대폭×0.25, 트랙 `(58,58,58)`), 채움 비율/색 CPU `(79,195,247)` 0.55, RAM `(129,199,132)` 0.85, GPU `(255,183,77)` 0.40. LANCZOS로 16, 20, 24, 32, 40, 48, 64, 128, 256 크기를 만들어 `icon.ico`에 모두 저장.

**build.bat** (CRLF 줄바꿈 필수 — LF면 cmd가 `goto`/레이블을 잘못 읽을 수 있음)
```bat
@echo off
setlocal
cd /d "%~dp0"
set PY=.venv\Scripts\python.exe
if not exist %PY% (
    echo [0/4] Creating venv with python.org Python 3.14...
    C:\Python314\python.exe -m venv .venv || goto :error
)
echo [1/4] Installing dependencies...
%PY% -m pip install --disable-pip-version-check -q -r requirements.txt || goto :error
echo [2/4] Creating icon...
if not exist assets\icon.ico (
    %PY% assets\make_icon.py || goto :error
)
echo [3/4] Building exe...
%PY% -m PyInstaller --noconfirm --clean --windowed --name PCMonitor --icon=assets\icon.ico ^
  --hidden-import=pynvml --hidden-import=pystray._win32 --hidden-import=PIL._tkinter_finder ^
  --hidden-import=uvicorn.logging --hidden-import=uvicorn.loops.auto ^
  --hidden-import=uvicorn.protocols.http.auto --hidden-import=uvicorn.protocols.websockets.auto ^
  --hidden-import=uvicorn.lifespan.on ^
  --add-data "templates;templates" --add-data "assets\icon.ico;assets" ^
  main.py || goto :error
echo [4/4] Done!
echo Output: dist\PCMonitor\PCMonitor.exe
if /i not "%~1"=="/nopause" pause
exit /b 0
:error
echo Build failed.
if /i not "%~1"=="/nopause" pause
exit /b 1
```
- `if ( … )` 블록 안 `echo`에 괄호를 쓰지 않는다(`)`가 블록을 닫음).
- PowerShell에서 실행: `& cmd.exe /c C:\waniwani\monitor\build.bat /nopause` (`cmd /c "build.bat /nopause"`처럼 묶으면 명령 이름으로 인식 못 함).
- **onedir만** 사용(`--onefile` 금지: 시작 느림, 템플릿 경로·백신 오탐). 결과 `dist\PCMonitor\` 폴더째 배포(약 50MB). 실행 메모리 약 100~150MB.
- 실행 중인 exe가 있으면 `--clean`이 `dist\PCMonitor` 삭제에 실패 → 먼저 종료.
- `nvml.dll`은 드라이버에 포함되므로 `--collect-binaries` 불필요. 빌드 경고 파일 `build\PCMonitor\warn-PCMonitor.txt`에서 핵심 모듈 누락 확인.
- exe로 실행되면: frozen 경로 분기(`sys._MEIPASS`), 대체 트레이 GUID가 exe 경로 기준으로 달라짐(정상), 자동 실행이 exe 경로로 교체.

---

## 8. 테스트

실행: `.venv\Scripts\python.exe -m unittest discover -s tests -t .` (`-t .` 필요 — `tests.test_display`의 `sample_data()`를 다른 테스트가 import). 현재 136개.

**원칙**
- 실제 사용자 설정/레지스트리를 건드리지 않는다: 설정은 `PC_MONITOR_HOME` 임시 폴더, 레지스트리는 `HKCU\Software\PCMonitorTest-<uuid>\...` 임시 키(끝나면 재귀 삭제), 웹서버는 `sync_auto_start`/notifier를 가짜로 주입.
- 수집/시간은 가짜 psutil·가짜 clock, 알림은 `sender` 주입 + `async_send=False`.
- 화면 API(작업표시줄, 커서 모니터, Z-order, 글꼴 레지스트리)는 이 PC에서 타입/범위만 확인, 없으면 skip.

| 파일 | 확인 내용 |
|---|---|
| test_config | 기본 파일 생성, 누락 키 채움, `tray_icon_style` 제거, 깨진 파일 `.bak`, 로드 시 잘못된 값 기본값, `network` 분리·디스크 대문자·중복 제거, 부분 중첩 update+version, 같은 값 version 유지, 잘못된 update는 키 경로 메시지+무변경, bool≠int, 반환값 복사본, 색 대문자, 오버레이 설정 범위와 6종 |
| test_collector | GPU 핸들 무효 시 30초 뒤 재초기화·시작 시 실패 재시도, 첫 샘플 속도 0·cdrom 제외·매핑 실패 None, 경과 시간 기반 속도, 자정 누적 초기화, 카운터 감소 0, 항목 실패 격리(로그), 분리된 디스크 건너뜀, 60초 히스토리, mock 형태 |
| test_display | 부하색 경계(50/50.4/80/80.1/None), format_rate, 원형 툴팁 예시·네트워크 묶음·빈/대기·GPU 없음·127자 축약/자름 |
| test_display_icons | 네트워크 단위(자연·즉시 상향·히스테리시스), 2글자 값, icon_face(비율·99 상한·없음·네트워크·히스테리시스 이월), 툴팁 문구, `format_amount_mb` |
| test_iconrender | label/badge 크기·내용, 없는 글꼴 대체, **`°` 간격이 글꼴별로(맑은 고딕, Segoe UI, DX영화자막) 최소 간격 근처로 줄고 겹치지 않음**(실제 렌더 픽셀로 측정), 6종×크기 strip 높이·hitbox 순서/범위, 낮은 작업표시줄(40px)에 맞춤, 데이터 없음, **값이 바뀌어도 폭 불변**, 네트워크 단위 이월, 미리보기, text 디자인 parts, LOOK_LABELS와 OVERLAY_LOOKS 일치 |
| test_graph | blend, 시리즈(cpu, net, gpu_temp, disk), auto_scale, 좌표 매핑, 오래된 샘플 제외·None 끊김, 값 상한, core_grid |
| test_popup | 팝업 위치(오른쪽 아래, 왼쪽 위, 음수 좌표 보조 모니터), 섹션 순서·묶음, 분리 디스크, popup_texts, 제목/보조 줄, 커서 모니터 API |
| test_overlay | compute_placement(정렬, 겹침, 딱 맞음, 앱 버튼 없음, 폭 0), **find_clock_left(글자 틈, 알림 종 포함, 넓은 틈에서 중단, 자기 오버레이 ignore, 내용 없음, 탐색 폭 밖)**, find_taskbars(주 notify 있음·보조 None), **find_covered_taskbars(전체화면이 위면 그 모니터만, 작업표시줄이 위면 아님, 최대화·부적격 창, 두 모니터 걸친 창)**, covered_taskbars 타입 |
| test_tray | desired_layout |
| test_notifyicon | GUID 결정성·경로/항목별 구분, 프로세스 경로, promote(없음/키 없음/새로 켬/사용자 설정 존중/대기 시간 초과) |
| test_notifier | 임계값 이하·같음은 알림 없음, CPU 지속 시간·하락 시 초기화, 즉시 알림+5분 쿨다운, 디스크별 키·문구, GPU 없음, 샘플당 1회, 꺼짐, 발송 예외 로그, 테스트 알림 |
| test_metrics_logger | 분 경계 평균, 부분 분 flush(중복 없음), GPU 없음 빈칸, 같은 날 재시작 헤더 유지, 날짜 넘김 새 파일 디스크, 7일 정리, 날짜 변경 시 정리, 잠김 파일 순서대로 재시도, poll 중복 방지 |
| test_startup | 개발 명령(pythonw 실제 경로+main.py), frozen 명령, 등록/유지/갱신/삭제·다른 프로그램 값 보존, 미등록 삭제 안전 |
| test_webserver | Host 허용 규칙, 인덱스(초기 설정 주입), 외부 Host 403, status/503, history/fonts, config GET, POST 반영+tray.wake, 잘못된 값 422, 폼 415, 배열 422·깨진 JSON 400, **auto_start 변경 시만 sync 호출**, 알림 테스트(200/415/503), 오버레이 미리보기 6종 PNG·잘못된 look 422·옛 `/api/tray-preview` 404·데이터 없음 |
| test_fonts | Segoe UI Semibold → seguisb.ttf, 앞뒤 공백, 없는 글꼴 None |

**수동 검증 방법 (자동화된 화면 확인)**
- DPI 인식 켠 파이썬에서 `ImageGrab.grab(bbox=작업표시줄 사각형, all_screens=True)`로 두 모니터 작업표시줄을 캡처해 직접 본다.
- 디자인 전환 검증: 실행 중인 앱에 `POST /api/config {"overlay_icon_look": …}`로 6종을 바꿔가며 캡처하고 원래 값으로 복원.
- 팝업 검증: 별도 스크립트에서 실제 `PopupWindow`를 띄워 10초 수집 후 `show()` → 창 영역 캡처.
- 대시보드: 브라우저 자동화로 열어 스크린샷·콘솔 오류 확인.
- 창 진단: `EnumWindows`로 TkTopLevel 창의 visible/rect/owner/exstyle, 작업표시줄보다 위인지(Z-order) 출력.

---

## 9. 결정 이력과 폐기한 방식 (다시 만들 때 같은 실수 금지)

| 주제 | 폐기/문제 | 최종 |
|---|---|---|
| CPU 온도·클럭 | psutil 온도 API 없음, WMI는 관리자·부정확, `cpu_freq` 고정값 | 수집 안 함 |
| 트레이 더블클릭 | pystray Win32는 더블클릭 미지원, 좌클릭과 충돌 | 좌클릭 팝업, 우클릭 메뉴 |
| 표시 수단 | ① 트레이 원형 아이콘 → ② 트레이 라벨형/배지형 숫자 아이콘(16px, 3x5→3x7 픽셀 글꼴까지 구현) → 사용자: 글자가 작고 `^` 오른쪽에만 놓임 | **오버레이만**, 트레이는 대체 수단. 숫자 트레이 아이콘·픽셀 글꼴 코드 삭제 |
| 16px 라벨 | 가로 굵게는 글자 속이 메워져 뭉개짐 | (삭제됨) |
| 작업표시줄 표시 방식 | TrafficMonitor식 작업표시줄 안 삽입은 Win11에서 막히고 업데이트마다 깨짐 | 작업표시줄 위 overrideredirect 창 |
| 가려짐/깜빡임 | 0.3초마다 TOPMOST 재지정 → 작업표시줄 클릭 시 깜빡임(사용자 거부) | **소유자=작업표시줄**(`GWLP_HWNDPARENT`) — 깜빡임 없음 확인 |
| 오버레이 모양 | 첫 데모는 두 줄 텍스트 → 사용자는 기존 아이콘 디자인을 원함 | 디자인 6종(두 줄 텍스트도 포함) |
| 오버레이 글꼴 | 32px 픽셀 글꼴 2배는 사용자 불만. 12개 후보 미리보기(픽셀, Bahnschrift SemiBold/Bold, Segoe UI Semibold/Bold/Black, Cascadia Mono, Consolas, Arial, Tahoma, 맑은 고딕, Noto Sans KR) | **Segoe UI Semibold** |
| 오버레이 클릭 | 클릭 통과 + 트레이로 조작 안(B) | 오버레이 직접 클릭(A) |
| 보조 모니터 | 트레이 영역 없음, 시계는 창 핸들 없음 | 픽셀로 시계 탐지. 틈 허용치 6px은 알림 종만 잡아 시계를 덮음 → 20px + 자기 창 ignore |
| 전체화면 판정 | ① `SHQueryUserNotificationState` 전역 → ② 전경 창 + QUNS: 한 모니터 전체화면 + 다른 모니터 창을 쓰면 **모든 모니터 오버레이가 숨음**(exe 검증 중 발견) | **Z-order에서 작업표시줄보다 위이면서 모니터를 덮는 창**, 모니터별, 200ms 빠른 확인 |
| `°` 간격 | ① 고정 16% 당김: Segoe UI는 겹침, DX영화자막은 부족 → ② `getbbox` 메트릭: DX영화자막 bbox가 칸 전체라 0 → ③ `°`만 측정: 원인이 뒤 글자 왼쪽 여백이라 0 | **실제 렌더 픽셀로 `°` 잉크 끝~뒤 글자 잉크 시작 측정**, 최소 8% 남김 |
| Python | Store판 3.13(샌드박스) | python.org 3.14 |
| pystray 아이콘 식별 | uID 버그로 모두 0, 매 실행 다른 아이콘으로 인식·숨김 영역 | 고정 GUID + `NIM_DELETE` 선행 + 자동 표시(`IsPromoted` 없을 때만) |
| 대시보드 주소 | `localhost`는 IPv6 먼저 시도해 느림 | `127.0.0.1` |
| 팝업 | RAM 큰 값이 길어 그래프가 창 밖으로, 코어 막대 폭 1px에서 그림, 빈 라벨로 높이 측정해 버튼 잘림 | 짧은 큰 값+보조 줄, `<Configure>` 재그림, 값 채운 뒤 배치 |
| 대시보드 설정 | 확대 미리보기가 그리드 열을 늘려 옆 섹션을 덮음 | `minmax(0, 1fr)`, `min-width:0`, 이미지 `max-width:100%` |
| 설정 검증 | Pydantic 모델 이중 관리 | `Config.update` 스키마 하나 |
| 토스트 | 임의 AppUserModelID는 Win11에서 표시 안 될 수 있음 | HKCU AUMID 등록 + 대시보드 테스트 버튼 |
| 폰트 목록 | PowerShell subprocess는 exe에서 콘솔 깜빡임·느림 | 메인 스레드 `tkinter.font.families()` |
| 네트워크 업로드 색 | `#EF5350`이 배지형의 "부하 높음" 빨강과 헷갈릴 수 있음(안내만, 미변경) | 대시보드 색 설정으로 변경 가능 |

---

## 10. 주의사항

- **스레딩**: tkinter는 메인 스레드만. pystray/웹/수집/알림/로그는 daemon 스레드. 콜백에서 UI가 필요하면 `root.after`.
- **ctypes**: HWND·HANDLE 인자/반환은 `argtypes`/`restype` 지정(64비트 핸들 오버플로). `GetLastError`는 `WinDLL(..., use_last_error=True)` + `ctypes.get_last_error()`.
- **DPI**: `SetProcessDpiAwareness(2)`를 tk 생성 전에. 모든 좌표는 물리 픽셀, 크기·간격은 작업표시줄/모니터 DPI 배율 곱.
- **음수 좌표**: 이 PC 보조 모니터는 `(1920, -726, 3360, 1834)`. 위치 계산·캡처에서 음수를 가정.
- **콘솔 없는 exe**: stdout/stderr None 대체, uvicorn `log_config=None`, subprocess가 필요하면 `CREATE_NO_WINDOW`.
- **에러 격리**: 수집 항목, 오버레이 갱신, 알림 발송, CSV 쓰기, 레지스트리 작업은 모두 예외를 로그로 삼키고 앱은 계속.
- **포트 충돌**: 18099 사용 중이면 대시보드만 비활성(메뉴/버튼 disabled), 나머지는 정상.
- **Pillow 12**: `Image.getdata()` 대신 `getchannel()`/`tobytes()`.
- **Windows 11 트레이**: 새 아이콘은 기본으로 숨김 영역, 드래그로 꺼내기도 버전에 따라 안 됨 → 자동 표시 기능.
- **개발 도구 제약(이 환경)**: PowerShell에서 긴 `Start-Sleep`(2분 이상)은 차단됨 → 기다림은 백그라운드 명령 루프로. bash heredoc은 역슬래시가 줄어들 수 있으니 Python 스크립트는 파일 쓰기 도구로 작성.

---

## 11. 개발 환경 참고 (이 PC)

- RTX 2060 (드라이버 617.42, 2026-10-12 업데이트), 드라이브 8개 C D E F G I J K(각각 다른 물리 디스크), RAM 31.8GB.
- 듀얼 모니터: 주 1920×1080(작업표시줄 y=1032~1080) + 보조 세로 1440×2560(원점 1920,-726, 작업표시줄 y=1786~1834). 둘 다 100% 배율, 라이트 작업표시줄.
- 사용자 오버레이 글꼴 설정 이력: DX영화자막 M(사용자 설치 글꼴). 디자인은 게이지형으로 바꿔 쓴 적 있음.
- 실행: 개발 `.venv\Scripts\python.exe main.py [--mock]`, exe `dist\PCMonitor\PCMonitor.exe`. 동시에 두 개는 뮤텍스로 막힘.
- 현재 자동 실행 등록: `"C:\waniwani\monitor\dist\PCMonitor\PCMonitor.exe"`.

---

## 12. 처음부터 다시 만들 때의 구현 순서

각 단계에서 테스트와 실제 동작(캡처/브라우저)을 확인한 뒤 다음으로 넘어간다.

1. **paths + config** — `python -m monitor.config`로 기본 파일 생성. test_config.
2. **winapi(드라이브 매핑) + collector** — `python -m monitor.collector`로 실제 값, `--mock`. test_collector.
3. **logger(app.log) + main 뼈대** — 뮤텍스, DPI, stdout 대체, tk root, 수집 스레드.
4. **notifyicon + tray(대체 아이콘)** — 고정 GUID, 자동 표시. 아이콘이 `^` 밖에 바로 보이는지 확인.
5. **display + fonts + iconrender** — 6종 렌더링, `python -m monitor.iconrender`로 미리보기 확인, `°` 간격 테스트.
6. **overlay** — 주 작업표시줄 → 보조(시계 탐지) → Z-order 전체화면 → 툴팁/메뉴 → 대체 아이콘 연동. 두 모니터 캡처로 확인.
7. **graph + popup** — 실제 팝업을 띄워 캡처(버튼 잘림·그래프 밀림·코어 막대 확인).
8. **webserver + dashboard** — TestClient 테스트, 브라우저로 메인/Settings 레이아웃·콘솔 오류 확인.
9. **notifier** — 판정 테스트 + 대시보드 테스트 알림.
10. **MetricsLogger** — 2분 기다려 실제 CSV 확인.
11. **startup** — 임시 키 테스트 + 실제 Run 값 확인(다른 항목 보존).
12. **make_icon + build.bat + README** — exe 빌드 → 개발 앱 종료 → exe 실행 → 로그/대시보드/두 모니터 캡처/Run 경로 확인.

---

## 13. 최종 확인 체크리스트

**오버레이**
- [ ] 실행 시 주 모니터 `^` 왼쪽, 보조 모니터 시계(및 알림 종) 왼쪽에 설정 순서대로 뜨고 트레이 아이콘은 없다
- [ ] 두 번 실행하면 두 번째는 조용히 종료
- [ ] 우클릭 "오버레이 디자인" 6종이 두 모니터 모두 1초 안에 바뀐다, 값이 바뀌어도 폭/위치가 흔들리지 않는다
- [ ] 작업표시줄 빈 곳/앱 버튼/시작 버튼을 클릭해도 깜빡이거나 가려지지 않는다
- [ ] 좌클릭 팝업, 우클릭 메뉴, 호버 툴팁 (두 모니터)
- [ ] 한 모니터에서 전체화면 영상 → 그 모니터만 즉시 숨김, **다른 모니터 창을 클릭해도** 다른 모니터 오버레이는 유지
- [ ] 앱 버튼이 늘어나 자리가 없으면 그 모니터만 숨김, 모든 모니터에 자리가 없거나 표시 항목이 없으면 원형 트레이 아이콘
- [ ] 자동 숨김 작업표시줄이면 함께 숨김, 탐색기 재시작 후 다시 붙음, 모니터 연결/분리를 따라감
- [ ] 라이트/다크 전환 시 글자색 변경, 테두리 번짐 없음
- [ ] `G°C`가 사용 글꼴에 관계없이 붙어 보인다
- [ ] 네트워크 KB→MB 전환, 경계에서 깜빡임 없음

**팝업**
- [ ] 커서가 있는 모니터 모서리에 뜨고(세로 보조 모니터 포함) 하단 버튼까지 잘리지 않는다
- [ ] 수치·그래프·코어 막대가 갱신, RAM/네트워크 그래프가 창 안에 있다
- [ ] 바깥 클릭·Esc로 닫힘, 안쪽 클릭은 유지, 열린 상태에서 오버레이 클릭 시 다시 열리지 않음

**대시보드**
- [ ] 팝업 버튼/우클릭으로 `127.0.0.1:18099`가 열리고 CPU·RAM·GPU·네트워크·드라이브 8개 표시
- [ ] tray 체크박스로 오버레이 항목 추가/제거
- [ ] Settings 미리보기 6종이 현재 수치로 보이고 선택 즉시 반영, 크기/글꼴/자동 실행 즉시 반영
- [ ] 화면/알림 설정 저장·잘못된 값 오류·되돌리기, 테스트 알림
- [ ] 우클릭 메뉴 변경이 5초 안에 Settings에 반영, 앱 종료 시 "연결 끊김" → 재시작 시 자동 복귀
- [ ] 외부 Host 403, 폼 POST 415
- [ ] 18099 포트 점유 시 앱은 동작하고 "대시보드 열기" 비활성

**알림 / 로그 / 자동 실행 / 빌드**
- [ ] CPU 부하 10초 이상 → 알림, 항목별 5분 쿨다운, 알림 끄기 동작, 테스트 알림에 한글·`°` 정상, 콘솔 깜빡임 없음
- [ ] 1분 후 CSV 행 추가(드라이브 열 포함), 종료 시 마지막 분 기록, 7일 지난 CSV 삭제, Excel로 잠가도 나중에 순서대로 기록
- [ ] 자동 실행 토글이 Run 값 `PCMonitor`만 등록/해제, 재부팅 후 오버레이 자동 표시
- [ ] `build.bat` → `dist\PCMonitor\PCMonitor.exe`, exe에서 오버레이·팝업·대시보드·알림 동작, Run 경로가 exe로 교체
