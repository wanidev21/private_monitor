# PC Monitor

Windows 11 작업표시줄에 CPU · RAM · GPU · 디스크 · 네트워크 수치를 실시간으로 표시하는 앱.

- **작업표시줄 오버레이**: 주 모니터는 `^`(트레이 영역) 왼쪽, 보조 모니터는 시계 왼쪽. 디자인 6종 (라벨형 / 배지형 / 게이지형 / 원형 링 / 미니 그래프형 / 두 줄 텍스트)
- **좌클릭**: 상세 팝업 (수치 + 최근 60초 그래프 + 코어별 막대)
- **우클릭**: 대시보드 열기 · 오버레이 디자인 · 알림 · Windows 시작 시 자동 실행 · 종료
- **웹 대시보드**: `http://127.0.0.1:18099` — 전체 현황, 표시 항목 선택, 디자인·글꼴·색상·알림 설정
- **알림**: CPU(10초 지속) / RAM / GPU 온도 / 디스크 사용률이 임계값을 넘으면 Windows 알림 (항목별 5분에 한 번)
- **CSV 로그**: 1분 평균, 날짜별 파일, 7일 보관

---

## 설치

### 1. 준비물

| 항목 | 설명 |
|---|---|
| Windows 11 | 다른 OS에서는 실행되지 않는다 |
| Python 3.11 이상 (**python.org 설치판**) | https://www.python.org/downloads/ — 개발·검증은 3.14. **Microsoft Store판 Python은 쓰지 않는다** (샌드박스라 자동 실행 레지스트리가 가상화되고 PyInstaller 빌드에 문제가 생김) |
| Git | https://git-scm.com/ (소스를 내려받을 때) |
| NVIDIA 그래픽 드라이버 | 선택. 없으면 GPU 항목만 `N/A`로 표시된다 |

Python 설치 시:
- "Add python.exe to PATH"는 선택 사항이다 (아래에서 전체 경로로 실행).
- `build.bat`은 기본으로 `C:\Python314\python.exe`를 찾는다. **Customize installation → Install location**을 `C:\Python314`로 지정하면 그대로 쓸 수 있다. 다른 위치에 설치했다면 아래 3-B처럼 가상환경을 먼저 만들면 된다.

### 2. 소스 받기

```bat
git clone https://github.com/wanidev21/private_monitor.git
cd /d private_monitor
```
(폴더 위치는 자유. 이후 명령은 그 폴더에서 실행)

### 3-A. exe로 빌드해서 쓰기 (권장)

```bat
build.bat
```
하는 일: 가상환경 `.venv` 생성(없을 때만) → 패키지 설치 → 아이콘 생성 → PyInstaller 빌드.

결과: `dist\PCMonitor\PCMonitor.exe` (폴더 전체 약 50MB)

- 실행: `dist\PCMonitor\PCMonitor.exe` 더블클릭.
- **`dist\PCMonitor\` 폴더째로** 원하는 곳(예: `C:\Program Files\PCMonitor` 또는 `D:\Apps\PCMonitor`)에 옮겨서 쓴다. exe만 따로 복사하면 동작하지 않는다.
- 처음 실행하면 "Windows 시작 시 자동 실행"이 켜진 상태로 등록된다. exe를 옮기면 다음 실행 때 새 경로로 다시 등록된다.
- 다시 빌드할 때는 실행 중인 PCMonitor를 먼저 종료한다 (`dist\PCMonitor`를 지우지 못해 빌드 실패).

PowerShell에서 실행할 때:
```powershell
& cmd.exe /c .\build.bat /nopause
```

### 3-B. Python이 `C:\Python314`가 아닌 곳에 있을 때

가상환경을 먼저 만들어 두면 `build.bat`은 생성 단계를 건너뛴다.
```bat
"<Python 설치 경로>\python.exe" -m venv .venv
build.bat
```
예: `"%LOCALAPPDATA%\Programs\Python\Python313\python.exe" -m venv .venv`

### 3-C. 빌드 없이 소스로 실행 (개발 모드)

```bat
"<Python 설치 경로>\python.exe" -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe main.py          :: 실제 수집
.venv\Scripts\python.exe main.py --mock   :: 더미 데이터
```
콘솔 창 없이 실행하려면 `.venv\Scripts\pythonw.exe main.py`.
개발 모드에서 자동 실행이 켜져 있으면 `pythonw.exe main.py`가 Run 레지스트리에 등록된다.

> PC Monitor는 동시에 하나만 실행된다. 이미 실행 중이면 두 번째 실행은 조용히 종료된다.

---

## 사용법

| 동작 | 결과 |
|---|---|
| 오버레이 좌클릭 | 상세 팝업 (바깥 클릭 또는 Esc로 닫힘) |
| 오버레이 우클릭 | 메뉴: 대시보드 열기 / 오버레이 디자인 / 알림 / 자동 실행 / 종료 |
| 오버레이에 마우스 올리기 | 항목별 툴팁 (예: `RAM 사용률 87% (13.9 / 16.0 GB)`) |
| 대시보드 | 카드의 `tray` 체크박스로 오버레이 항목·순서 선택, `⚙ Settings`에서 디자인·크기·글꼴·색상·갱신 주기·알림 임계값 설정, 테스트 알림 |

### 오버레이 표시 항목
CPU, RAM, GPU 사용률, GPU 온도(`G°C`), 다운로드(`↓`), 업로드(`↑`), 드라이브별 사용률(`C:` 등). 기본값은 CPU · RAM · GPU · GPU 온도 · ↓ · ↑.

### 숫자 색
초록 ≤ 50, 주황 ≤ 80, 빨강 > 80 (GPU 온도는 °C 값 그대로 적용).

### 오버레이가 숨는 경우
- 그 모니터에 전체화면 창(영상·게임)이 떠 있을 때 (모니터별로 판단)
- 작업표시줄 자동 숨김 중일 때
- 앱 버튼이 많아 오버레이 자리가 없을 때 — 모든 모니터에 자리가 없으면 트레이(`^` 안)에 원형 아이콘이 대신 나타난다

---

## 삭제

1. 오버레이 우클릭 → "Windows 시작 시 자동 실행" 끄기 → "종료"
2. 설치 폴더(`dist\PCMonitor` 또는 옮긴 폴더)와 소스 폴더 삭제
3. 설정·로그를 지우려면 `%USERPROFILE%\.pc-monitor` 폴더 삭제
4. (선택) 레지스트리 `HKCU\Software\Classes\AppUserModelId\PCMonitor.SystemMonitor` 삭제 (알림 발신자 이름)

---

## 파일 위치

| 내용 | 경로 |
|---|---|
| 설정 | `%USERPROFILE%\.pc-monitor\config.json` |
| CSV 로그 | `%USERPROFILE%\.pc-monitor\logs\YYYY-MM-DD.csv` |
| 앱 오류 로그 | `%USERPROFILE%\.pc-monitor\logs\app.log` |
| 자동 실행 | 레지스트리 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` → `PCMonitor` |

CSV 열: `timestamp, cpu_pct, ram_pct, gpu_pct, gpu_temp, net_down_kb, net_up_kb, disk_<문자>_pct…`

---

## 문제 해결

- **오버레이가 안 보임**: 위 "오버레이가 숨는 경우" 확인. 표시 항목을 모두 끄면 원형 트레이 아이콘만 보인다.
- **원형 트레이 아이콘이 `^` 안에 숨어 있음**: `설정 > 개인 설정 > 작업 표시줄 > 기타 시스템 트레이 아이콘`에서 켠다.
- **대시보드가 열리지 않음**: 18099 포트를 다른 프로그램이 쓰고 있으면 대시보드가 비활성화된다 (`app.log`에 `web port 18099 in use`). `config.json`의 `web_port`를 바꾸고 앱을 다시 실행한다.
- **알림이 안 뜸**: Windows 방해 금지가 켜져 있으면 화면에 뜨지 않고 알림 센터에만 쌓인다. 대시보드 Settings의 "테스트 알림 보내기"로 확인.
- **GPU가 N/A**: NVIDIA 드라이버가 설치되어 있어야 한다 (`nvml.dll`은 드라이버에 포함). AMD/Intel GPU는 지원하지 않는다.
- **디스크 읽기/쓰기 속도가 `-`**: 스토리지 공간·동적 디스크 등 물리 디스크 매핑이 안 되는 드라이브.
- **빌드 실패**: `C:\Python314\python.exe`가 없으면 3-B 방법 사용. PCMonitor.exe가 실행 중이면 종료 후 다시 빌드. 누락 모듈은 `build\PCMonitor\warn-PCMonitor.txt` 확인.
- **그 밖의 오류**: `app.log`를 확인한다.

---

## 개발

```bat
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m unittest discover -s tests -t .
```

| 명령 | 용도 |
|---|---|
| `python -m monitor.config` | 설정 경로·내용 출력 |
| `python -m monitor.collector [--mock]` | 수집 결과 1회 출력 |
| `python -m monitor.iconrender [--out PNG] [--size N]` | 오버레이 디자인 6종 미리보기 이미지 |

구조·알고리즘·설계 결정은 [CLAUDE.md](CLAUDE.md) 참조.
