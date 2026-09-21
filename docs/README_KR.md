# Grounded — 빠른 시작 (한국어)

> 요약 번역입니다. [영어 README](https://github.com/gonisulaimann/Grounded#readme)가 공식 문서입니다.

**Grounded**는 코드 주석의 허공 참조를 찾습니다: 존재하지 않는 함수, 누락된 파일, 깨진 임포트. 결정적, 오프라인, 의존성 제로. Python, JavaScript/TypeScript, Go, C를 지원합니다.

## 설치

```console
pip install grounded-lint
```

```console
brew install gonisulaimann/tap/grounded
```

## 사용법

```console
grounded scan .                    # 저장소 검사 (발견 시 종료 코드 1)
grounded scan . --changed          # 변경된 줄만 검사 (CI에 적합)
grounded fix . --dry-run           # 수정 내용 미리보기
grounded impact my_function .      # 심볼 관련 전체 정보: 정의, 임포트 위치
```

## 규칙

| ID | 심각도 | 검사 내용 |
|---|---|---|
| `stale-symbol-ref` | lie | 주석이 가리키는 함수가 저장소에 없음 |
| `stale-import` | lie | 해석 가능한 임포트가 누락된 모듈이나 미정의 이름을 가리킴 |
| `stale-file-ref` | lie | 주석이 가리키는 경로가 저장소에 없음 |
| `number-drift` | drift | 주석의 숫자가 인접 코드와 모순됨 |
| `fragile-anchor` | smell | 깨지기 쉬운 줄 앵커, 티켓 없는 workaround 표시 |

## 링크

- [전체 문서](https://grounded.readthedocs.io/en/latest/) (영어)
- [이슈 제보](https://github.com/gonisulaimann/Grounded/issues)
- 라이선스: MIT
