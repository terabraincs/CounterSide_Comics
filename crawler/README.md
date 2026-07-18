# 컨텐츠 크롤러

네이버 게임 라운지의 공개 게시글과 카운터사이드 공식 홈페이지의 공개 미디어를 수집하는 Python 명령줄 스크립트 모음입니다.

## 네이버 라운지 크롤러

네이버 게임 라운지에서 로그인 없이 볼 수 있는 공개 게시글을 수집하는 Python 명령줄 프로그램입니다. 게시글 목록과 본문을 읽어 JSON·CSV로 저장하거나, 본문 이미지만 게시글 제목별 폴더에 내려받을 수 있습니다.

### 주요 기능

- 라운지 전체 또는 특정 게시판의 공개 글 수집
- 여러 페이지를 마지막 페이지까지 자동 순회
- 상단 고정글 포함 또는 제외
- 스마트에디터 JSON 본문과 기존 HTML 본문 분석
- 제목, 작성자, 게시판, 작성일, 조회수, 댓글 수, 반응 수 저장
- 본문 텍스트, 이미지 주소, 외부 링크 추출
- JSON, CSV 또는 두 형식 동시 출력
- 본문 이미지 원본 다운로드
- 게시글 제목별 이미지 폴더 생성
- 요청 간 지연, 타임아웃 및 실패 재시도 지원
- 파일을 만들지 않는 드라이런 지원

### 실행 환경

- Python 3.10 이상
- 인터넷 연결
- 별도 외부 Python 패키지 불필요

### 기본 실행

```powershell
python .\naver_lounge_crawler.py
```

기본 설정은 `COUNTERSIDE` 라운지에서 고정글을 포함해 최신 공개 글 30개를 수집하고 `output` 폴더에 JSON과 CSV를 저장합니다.

다른 라운지를 수집하려면 주소의 `/lounge/` 다음에 있는 라운지 ID를 지정합니다.

```powershell
python .\naver_lounge_crawler.py --lounge LOUNGE_ID
```

### 파일을 만들지 않고 확인하기

드라이런은 실제 목록과 본문을 가져와 분석하지만 파일을 저장하지 않습니다.

```powershell
python .\naver_lounge_crawler.py `
  --lounge LOUNGE_ID `
  --max-posts 5 `
  --dry-run `
  --delay 0
```

### 특정 게시판 수집

게시판 주소의 마지막 숫자를 `--board-id`로 지정합니다.

```powershell
python .\naver_lounge_crawler.py `
  --lounge LOUNGE_ID `
  --board-id BOARD_ID `
  --max-posts 100
```

`--all`을 사용하면 최대 글 수 제한 없이 마지막 페이지까지 수집합니다.

```powershell
python .\naver_lounge_crawler.py `
  --lounge LOUNGE_ID `
  --board-id BOARD_ID `
  --all `
  --no-pins
```

### 이미지 다운로드

일반 출력과 함께 본문 이미지를 내려받습니다.

```powershell
python .\naver_lounge_crawler.py `
  --lounge LOUNGE_ID `
  --max-posts 100 `
  --download-images
```

이 경우 이미지는 다음 구조로 저장됩니다.

```text
output/
└─ images/
   └─ <글 번호>/
      ├─ 001_<원본 파일명>.jpg
      └─ 002_<원본 파일명>.png
```

### 게시글 제목별 이미지 폴더

JSON·CSV 없이 이미지 파일만 정리하려면 다음 옵션을 함께 사용합니다.

```powershell
python .\naver_lounge_crawler.py `
  --lounge LOUNGE_ID `
  --board-id BOARD_ID `
  --all `
  --no-pins `
  --download-images `
  --title-folders `
  --images-only `
  --output-dir ".\downloaded_images"
```

출력 구조는 다음과 같습니다.

```text
downloaded_images/
├─ 게시글 제목 A/
│  └─ 1.jpg
└─ 게시글 제목 B/
   ├─ 1.jpg
   ├─ 2.jpg
   └─ 3.png
```

이미지 수와 관계없이 본문 표시 순서대로 `1`, `2`, `3` 이름을 사용합니다. 이미지가 한 장이어도 `1.jpg`처럼 저장하며, 확장자는 실제 이미지 형식에 맞춰 정합니다.

Windows에서 폴더명으로 사용할 수 없는 `?`, `:`, `/` 등의 문자는 `_`로 변경됩니다. 같은 제목이 중복되면 뒤쪽 폴더명에 글 번호를 붙입니다.

### 출력 데이터

JSON 파일은 수집 정보인 `metadata`와 게시글 배열인 `posts`로 구성됩니다. 각 게시글에는 다음 값이 포함됩니다.

- 글 번호와 원본 주소
- 라운지 ID와 이름
- 게시판 ID와 이름
- 제목과 작성자 정보
- 작성일과 수정일
- 조회수와 댓글 수
- 버프·너프 수
- 고정글 여부
- 본문 형식, 일반 텍스트 및 원본 본문
- 대표 이미지와 본문 이미지 주소
- 본문에서 발견한 링크

CSV는 표 계산 프로그램에서 다루기 쉬운 평면 형태로 저장됩니다. JSON 파일은 UTF-8, CSV 파일은 UTF-8 BOM 형식을 사용합니다.

### 주요 옵션

| 옵션 | 설명 | 기본값 |
| --- | --- | --- |
| `--lounge` | 수집할 라운지 ID | `COUNTERSIDE` |
| `--board-id` | 특정 게시판 ID | 전체 게시판 |
| `--max-posts` | 고정글을 포함한 최대 글 수 | `30` |
| `--all` | 모든 페이지 수집 | 사용 안 함 |
| `--order` | API 정렬 값 | `NEW` |
| `--page-size` | 한 페이지에서 요청할 글 수 | `30` |
| `--no-pins` | 상단 고정글 제외 | 사용 안 함 |
| `--output-dir` | 결과 저장 폴더 | `output` |
| `--format` | `json`, `csv`, `both` 중 선택 | `both` |
| `--download-images` | 본문 이미지 다운로드 | 사용 안 함 |
| `--title-folders` | 게시글 제목별 이미지 폴더 사용 | 사용 안 함 |
| `--images-only` | JSON·CSV를 만들지 않음 | 사용 안 함 |
| `--delay` | 요청 사이 대기 시간(초) | `0.5` |
| `--timeout` | 요청 제한 시간(초) | `20` |
| `--retries` | 일시적 실패 재시도 횟수 | `3` |
| `--dry-run` | 파일 저장 없이 수집 확인 | 사용 안 함 |

전체 도움말은 다음 명령으로 확인할 수 있습니다.

```powershell
python .\naver_lounge_crawler.py --help
```

### 테스트

프로젝트의 상위 폴더에서 다음 명령을 실행합니다.

```powershell
python -m unittest tests.test_crawler -v
```

테스트는 인터넷 연결 없이 다음 항목을 검사합니다.

- JSON형·HTML형 본문 파싱
- 이미지와 링크 추출
- 페이지 번호 증가와 전체 페이지 순회
- 고정글 중복 제거
- 제목 폴더명과 이미지 번호 생성
- JSON·CSV UTF-8 저장

### 주의사항

- 공개된 게시글만 읽으며 로그인이나 접근 제한을 우회하지 않습니다.
- 삭제된 글, 비공개 글, 클린봇으로 가려진 본문은 수집할 수 없습니다.
- 전체 수집은 요청 수와 이미지 용량이 커질 수 있으므로 충분한 저장 공간과 적절한 `--delay` 값을 사용하세요.
- 네이버 게임의 응답 형식이 변경되면 크롤러 수정이 필요할 수 있습니다.
- 수집한 게시글과 이미지의 이용 및 재배포 책임은 사용자에게 있습니다.

---

## 카운터사이드 공식 홈페이지 미디어 크롤러

`counterside_media_crawler.py`는 카운터사이드 공식 홈페이지의 공개 미디어 목록과 상세 페이지를 읽어 JSON·CSV와 원본 이미지를 저장하는 Python 명령줄 프로그램입니다.

### 주요 기능

- 미디어 목록에 표시된 모든 페이지 자동 순회
- 미디어 번호를 기준으로 중복 항목 제거
- 제목, 작성자, 게시일, 게시 시각, 상세 주소 수집
- 썸네일과 상세 페이지의 원본 이미지 주소 추출
- JSON, CSV 또는 두 형식 동시 출력
- 미디어 번호별 또는 제목별 이미지 폴더 선택
- 기존 이미지 건너뛰기와 강제 덮어쓰기
- 요청 지연, 타임아웃 및 실패 재시도 지원
- 파일을 만들지 않는 드라이런 지원

### 기본 실행

```powershell
python .\counterside_media_crawler.py
```

기본 목록은 일본어 가이드 웹툰입니다.

```text
https://www.counterside.com/media/lists/ct/jp/tbl/media/cate/guiwt
```

`--output-dir`를 생략하면 스크립트가 있는 `CounterSide_Comics` 폴더의 `output`에 저장됩니다. 필요한 폴더는 자동으로 생성됩니다.

```text
output/
├─ counterside_jp_guide_webtoons.json
├─ counterside_jp_guide_webtoons.csv
└─ images/
   └─ <미디어 번호>/
      └─ 1.jpg
```

### 출력 폴더 지정

상대 `--output-dir` 값은 `CounterSide_Comics`를 기준으로 해석합니다.

```powershell
python .\counterside_media_crawler.py --output-dir ".\KOR\공식 웹툰"
```

위 명령은 `CounterSide_Comics/KOR/공식 웹툰`에 저장합니다. 절대 경로를 지정하면 해당 경로를 그대로 사용합니다.

### 제목별 이미지 폴더

`--title-folders`를 사용하면 미디어 번호 대신 제목을 폴더명으로 사용합니다.

```powershell
python .\counterside_media_crawler.py `
  --output-dir ".\JPN\ガイドウェブトゥーン" `
  --title-folders
```

```text
JPN/ガイドウェブトゥーン/
├─ counterside_jp_guide_webtoons.json
├─ counterside_jp_guide_webtoons.csv
└─ カラオケがライブ会場/
   └─ 1.jpg
```

이미지 수와 관계없이 표시 순서대로 `1`, `2`, `3` 이름을 사용합니다. 확장자는 원본 이미지 형식을 유지합니다. Windows 폴더명으로 사용할 수 없는 문자는 `_`로 변경하며, 제목이 중복되면 미디어 번호를 붙입니다.

### 영문 가이드 웹툰

```powershell
python .\counterside_media_crawler.py `
  --list-url "https://www.counterside.com/media/lists/ct/en/tbl/media/cate/guiwt" `
  --output-dir ".\ENG\Guide Comics" `
  --title-folders
```

### 파일을 만들지 않고 확인하기

```powershell
python .\counterside_media_crawler.py --dry-run --delay 0
```

### 주요 옵션

| 옵션 | 설명 | 기본값 |
| --- | --- | --- |
| `--list-url` | 수집할 미디어 목록 주소 | 일본어 가이드 웹툰 |
| `--output-dir` | `CounterSide_Comics` 기준 상대 경로 또는 절대 경로 | `output` |
| `--max-items` | 최대 수집 항목 수, `0`이면 전체 | `0` |
| `--format` | `json`, `csv`, `both` 중 선택 | `both` |
| `--metadata-only` | 이미지를 받지 않고 목록만 저장 | 사용 안 함 |
| `--title-folders` | 제목별 이미지 폴더 사용 | 사용 안 함 |
| `--overwrite` | 기존 이미지도 다시 다운로드 | 사용 안 함 |
| `--delay` | 요청 사이 대기 시간(초) | `0.5` |
| `--timeout` | 요청 제한 시간(초) | `20` |
| `--retries` | 일시적 실패 재시도 횟수 | `3` |
| `--dry-run` | 파일 저장 없이 수집 확인 | 사용 안 함 |

전체 도움말은 다음 명령으로 확인할 수 있습니다.

```powershell
python .\counterside_media_crawler.py --help
```

### 출력 데이터

JSON은 `metadata`와 `items`로 구성되며 다음 정보를 저장합니다.

- 미디어 번호와 제목
- 작성자
- 게시일과 게시 시각
- 원본 상세 페이지 주소
- 썸네일 주소
- 원본 이미지 주소
- 저장된 이미지의 상대 경로
- 상세 페이지의 텍스트 본문

CSV에는 주요 값을 표 형식으로 저장합니다. JSON은 UTF-8, CSV는 UTF-8 BOM 형식을 사용합니다.

### 주의사항

- 로그인 없이 공개된 미디어만 읽으며 접근 제한을 우회하지 않습니다.
- 전체 수집은 요청 수와 이미지 용량이 커질 수 있으므로 적절한 `--delay` 값을 사용하세요.
- 공식 홈페이지의 HTML 구조가 변경되면 크롤러 수정이 필요할 수 있습니다.
- 수집한 이미지의 이용 조건과 재배포 기준을 확인한 뒤 사용하세요.
