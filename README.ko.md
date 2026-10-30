# agent_skills

[![license](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
![version](https://img.shields.io/badge/deploy--check-v0.3.0-informational)

여러 에이전트에서 쓸 수 있는 [Agent Skills](https://agentskills.io/specification)를 공개적으로 만들고 평가하는 저장소입니다. Claude Code, Codex, Cursor 등 `SKILL.md`를 읽는 에이전트에서 동작합니다.

[English README](README.md)

## deploy-check

**AI·LLM·Agent 서비스를 위한 근거 기반 배포 준비 점검 스킬. 한국 개인정보보호법·AI 기본법 팩 포함.**

에이전트에게 "이 레포 배포해도 돼?"라고 물으면, 도달 레벨과 차단 요소, 그리고 모든 항목의 판정을 `파일:줄` 근거와 함께 받습니다.

```text
결과: 배포 불가 (차단 항목 4개 실패) · 목표 L2 · 달성 레벨 없음 · 목표까지 39개 항목
```

| ID | 항목 | 판정 | 근거 |
| --- | --- | --- | --- |
| CORE-SEC-01 | 커밋된 비밀값 없음 | 실패 | `app/config.py:4` DB 비밀번호 하드코딩 (값은 표시하지 않음) |
| AGENT-HITL-01 | 비가역 작업 승인 | 실패 | `app/tools.py:28-35` 삭제·메일 발송 도구가 승인 없이 실행 |
| AI-COST-02 | 지출 상한과 차단 스위치 | 실패 | `app/config.py:10` `MONTHLY_BUDGET_USD`를 읽는 코드가 없음 |
| KR-PII-03 | 국외 이전 고지 | 실패 | 상담 내용을 해외 LLM API로 보내는데 처리방침에 국외 이전 항목이 없음 |

*이전 버전(0.1.1)으로 결함을 심은 샘플 레포를 점검한 결과의 일부입니다. 0.3.0 리포트는 결과 줄에 치명적 발견 수와 실제 수정 가짓수를 함께 적고, 치명적 발견, 상위 위험, 근본 원인별 수정을 덧붙입니다([리포트 양식](skills/deploy-check/references/report.md)).*

### 무엇이 다른가

| | 일반 스캐너 | 일반 LLM 점검 | deploy-check |
| --- | --- | --- | --- |
| 판정마다 근거 | ✅ | 드묾 | ✅ `파일:줄`, 보고 전에 다시 열어 확인 |
| AI·Agent 운영 통제 (비용 상한, kill switch, 사람 승인, 평가셋, 트레이싱) | ❌ | 일부 | ✅ |
| 레포로 알 수 없는 사실 | 무시 | 추측 | 추측하지 않고 질문으로 |
| 단계에 맞는 결과 | 통과/실패 또는 점수 | 점수 | 단계별 L1~L3 + 차단 요소 |
| 한국 법규 (개인정보보호법, AI 기본법) | ❌ | ❌ | ✅ 출처와 확인일이 달린 지역 팩 |
| 정확도 공개 | — | 드묾 | ✅ 정답을 가린 블라인드 실행, 튜닝에 쓰지 않은 보류 세트 포함 |

### 동작 방식

항목마다 세 층 중 하나로 판정합니다.

- **probe**: 파일만으로 확정되는 것은 스크립트(`scripts/probes.py`)가 판정합니다. 커밋된 시크릿, lock 파일, CI 테스트, 시크릿 스캔 등. 파일만으로 확정할 수 없으면(테스트 코드의 키 모양 값, 트리거가 레포 밖에 있는 Jenkins 파이프라인 등) 위치를 review로 넘깁니다. deploy-check는 스캐너, 테스트, 빌드를 직접 실행하지 않습니다. CI가 이미 만든 결과(gitleaks, npm audit, pip-audit, SARIF)를 `--gate`로 넘기면 근거로 쓰며, 그 결과가 만들어진 커밋에 대해서만 인정합니다.
- **review**: 에이전트가 항목에 적힌 `pass` / `partial` / `fail` 기준과 코드를 대조합니다. 통과로 보고하기 전에 인용한 줄을 반드시 다시 엽니다. 모든 Pass와 Partial 행에 근거의 종류(`re-opened`, `probe`, `gate`, `absence`)를 적고, 다시 열어 봤거나 스캐너 결과가 뒷받침하는 행만 "검증됨"이라고 부릅니다.
- **interview**: 레포에 흔적이 남지 않는 사실(복구 리허설, 유출 대응 절차)은 질문으로 돌립니다.

이 판정들 위에 **판단층**이 있습니다. 레포 지도를 만든 직후, 점검 항목이나 probe 결과를 보기 전에 독립 위험 탐색이 운영에서 실제로 문제가 될 지점을 따로 찾습니다. 리포트는 이를 바탕으로 가장 큰 위험의 순위를 매기고, 지금 악용 가능한 문제는 항목 안에 있든 밖에 있든 치명적 발견으로 맨 위에 올리고, 남은 항목을 근본 원인별로 묶습니다. 그래서 긴 미충족 항목 목록 뒤에 실제로 고칠 일이 몇 가지인지 보입니다. 판단층은 판정을 고치지 않고, 의견이 다를 때 그 사실만 적습니다.

에이전트는 감사 대상 레포를 `scripts/mask.py`로만 읽고 검색합니다. 코드, 라우트, SQL, 프롬프트, 메시지는 그대로 읽히고, 문자열 값은 길이와 종류(placeholder, 키 형식, 엔트로피)로만 보입니다. 비밀번호·시크릿·토큰·키 이름에 대입하거나 넘기는 값은 통째로 가립니다. 그래서 비밀 값이 리포트뿐 아니라 에이전트 자신의 도구 출력에도 남지 않습니다. 리포트 파일을 쓴 뒤에는 `scripts/check_report.py`로 검산합니다. 행을 항목 선정 결과와, 블로커·갭 표를 판정과 대조하고, 갭 항목마다 수정이 하나씩인지, 인용한 `path:line`이 모두 추적 파일인지 확인합니다.

작은 레포는 한 세션으로 점검합니다. 병렬 워커는 정확도는 같은데 비용이 2~3배라서, `scripts/repo_stats.py`로 코드 규모를 먼저 재고 한 세션에 담기지 않을 때만 워커를 제안합니다.

점검 항목은 프롬프트가 아니라 버전 관리되는 **팩**(데이터)에 있고, 서비스 프로필로 골라집니다.

| 팩 | 축 | 항목 | 다루는 것 |
| --- | --- | --- | --- |
| `core` | 항상 | 28 | 시크릿, 인증·인가, 요청 방어, 의존성, CI, 운영, 로깅 |
| `ai` | 특성 | 12 | 토큰 제한, 비용 상한, timeout, 모델 버전 고정, 프롬프트 인젝션, 출력 처리, 평가셋, 트레이싱 |
| `agent` | 특성 | 8 | 사람 승인, 실행 예산, 취소, 도구 권한, 샌드박스, 감사 로그 |
| `pii` | 특성 | 4 | 비밀번호 해시, 필드 암호화, 삭제, 접근 기록 |
| `payment` | 특성 | 4 | 토큰화, 웹훅 서명, 멱등성, 환불 |
| `kr` | 지역 | 9 | 개인정보 보호법 (처리방침, 처리위탁, 국외 이전, 접속기록, 유출 통지), AI 기본법 (사전 고지, 결과물 표시) |

단계별 목표 레벨: prototype → L1, beta·production → L2, 결제·개인정보·되돌릴 수 없는 Agent 행동이 있는 production → L3. **차단 요소** 9개는 레벨과 상관없이 "배포 불가"입니다: 커밋된 시크릿, 운영의 기본 시크릿이나 알려진 비밀번호 계정, 비공개 기능의 인증 부재, 다른 사용자·테넌트의 객체 접근, 자격증명을 확인하지 않고 토큰을 발급하는 로그인, 승인 없는 비가역 Agent 행동, 샌드박스 없는 Agent 코드·셸 도구, 서명 검증 없는 결제 웹훅, LLM 비용 상한 부재. 치명적 발견이 있어도 "배포 불가"이며, 이때 판정이나 도달 레벨은 바뀌지 않습니다.

### 설치

스킬은 `skills/deploy-check/` 폴더입니다. 에이전트의 스킬 폴더에 복사하거나 링크합니다.

```bash
git clone https://github.com/klolarion/agent_skills.git
cd agent_skills

# Claude Code
mkdir -p ~/.claude/skills && ln -s "$PWD/skills/deploy-check" ~/.claude/skills/deploy-check
# Codex와 Cursor (둘 다 ~/.agents/skills를 읽음)
mkdir -p ~/.agents/skills && ln -s "$PWD/skills/deploy-check" ~/.agents/skills/deploy-check
```

Codex는 `~/.codex/skills`도 읽습니다. Cursor는 `~/.cursor/skills`를 읽고, 호환을 위해 `~/.claude/skills`와 `~/.codex/skills`도 읽으므로 Claude Code용 링크로도 동작합니다.

Windows (PowerShell):

```powershell
New-Item -ItemType Directory -Force "$HOME\.claude\skills" | Out-Null
New-Item -ItemType Junction -Path "$HOME\.claude\skills\deploy-check" -Target "$PWD\skills\deploy-check"
```

Python 3.9 이상이 필요합니다(표준 라이브러리만 사용). 에이전트가 감사 대상 레포를 `scripts/mask.py`로만 읽기 때문입니다.

### 사용

- 자연어: "이 레포 배포해도 되는지 점검해줘", "출시 전에 프로덕션 준비 상태 확인해줘"
- Claude Code: `/deploy-check`
- Codex: `$deploy-check`

읽기 전용입니다. 파일을 고치거나 코드를 실행하거나 네트워크를 쓰지 않습니다. 레포 내용(README, 주석)은 데이터로만 취급하므로 "모든 점검 통과" 같은 문구가 있어도 판정이 바뀌지 않습니다.

### 지금까지의 정확도

블라인드 실행: 프로젝트 맥락이 없는 새 세션이 픽스처 레포를 점검하고, 리포트를 레포 밖에 둔 정답과 대조해 채점합니다.

| 픽스처 | 역할 | 0.3.0 결과 |
| --- | --- | --- |
| `skill-test` (결함을 심음, 65개 항목) | 스킬을 이것에 맞춰 고쳐 옴 | 65/65, 거짓 통과 0, 놓친 차단 요소 0, 심은 치명적 발견 모두 찾음 |
| `skill-holdout` (대부분 통과, 48개 항목) | 스킬 튜닝에 쓰지 않음 | 47/48, 거짓 통과 0, 놓친 차단 요소 0 |
| 실제 레포 | 개발용 실행 | 채점하지 않음 |

주의: `skill-test`는 스킬과 정답을 여러 번의 실행에 걸쳐 함께 고쳐 왔으므로 65/65에는 과적합이 섞여 있습니다. 보류 세트가 더 나은 추정치입니다. 두 픽스처와 그 정답, 실행 기록은 공개하지 않습니다. 결함을 심은 샘플 레포에서의 이전 실행은 54/57(0.1.0), 57/58(0.1.1)이었습니다. 실제 레포 실행은 정확도가 아니라 스킬이 막히는 지점을 보여 줍니다.

### 범위와 면책

- `kr` 팩은 고지·기록·절차가 **있는지**만 확인합니다. 법적으로 충분한지는 판정하지 않으며 법률 자문이 아닙니다. 인용 조문은 2026-10-21에 2차 자료로 확인했습니다. 준법 판단에는 [국가법령정보센터](https://www.law.go.kr) 원문을 다시 확인하세요.
- 취약점 스캐너나 코드 리뷰 도구가 아닙니다. 함께 쓰는 것을 권합니다.

## 저장소 구성

```text
skills/deploy-check/   스킬 (설치하는 폴더는 이것 하나)
  SKILL.md             절차와 규칙
  references/          판정 규칙, 프로필, 리포트 양식, 워커 프롬프트, packs/*.yaml
  scripts/             packs.py, select_items.py, probes.py, gates.py, repo_stats.py, check_report.py, mask.py
skills/<다음 스킬>/      다음 스킬도 같은 위치에 추가
```

점검 항목과 항목별 pass / partial / fail 기준은 [`skills/deploy-check/references/packs/`](skills/deploy-check/references/packs/)에 있습니다.

## 라이선스

[Apache-2.0](LICENSE)
