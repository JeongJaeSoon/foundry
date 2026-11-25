---
marp: true
theme: default
paginate: true
---

# foundry

한 사람이 책임지는 가운데 여러 작업 단위를 병렬로 진행하는
Claude Code 플러그인.

전체 그림은 `docs/concepts.md` 에 있다.

---

## 1. 문제

- 한 사람에게 많은 티켓, 많은 저장소, 많은 Claude 세션.
- 세션은 규칙을 잊고, 끝없이 재시도하고, 폴링으로 기다리고, 무엇이든 사람에게 묻는다.
- 운영, 비밀 정보, 밤 push 에는 사람이 필요하다. 나머지는 필요 없어야 한다.

foundry 는 사람을 결정에만 두고 나머지는 세션에 맡긴다.

---

## 2. 사슬

```
owner ──채팅──> foreman ──launch/resume──> line-lead (unit 마다 1)
                   │                   └─> shop-boss (repo 마다 1)
                   │                          │
                   └─ dispatcher, judge       └─Agent─> hands (light / 보통 / heavy)
```

- owner 는 foreman 하고만 말한다.
- lead 는 각자 독립된 `--bg` 세션이다. lead 끼리는 말하지 않는다.
- 사슬을 건너뛰는 호출(owner→lead, foreman→hand)은 hook 이 거부한다.

---

## 3. 규칙은 기억이 아니라 hook 에

- PreToolUse, PermissionRequest, SessionStart, Stop 같은 hook 이 사슬,
  git 정책, Slack 조용한 시간과 중복 방지, 바깥으로 나가는 글의 footer 를 강제한다.
- 프롬프트에만 있는 규칙은 바람이고, hook 은 보장이다.
- 거부에는 고친 명령이 붙어 있어서 모델은 맞는 형태로 한 번만 다시 친다.

---

## 4. 단계별 승인

```
tool 호출 ─> auto mode 분류기 ─거부─> 확정 (triage: 대안 / 재시도 / owner 카드)
                 │
                 └─ask 규칙─> tier 0 규칙 ─> tier 1 장부 ─> tier 2 foreman (+advisor)
                                  └─ owner 전용 ──────────────> tier 3 owner
```

- owner 전용: 운영, 비밀 정보와 로그인, 설정, 결제, 공개 배포.
- tier 3 는 보드에 카드로 뜬다: 이유, 근거, advisor 메모, allow / deny.

---

## 5. 상태는 journal 하나

- 모든 사실은 `journal.jsonl` 에 한 줄씩 덧붙고, snapshot 은 거기서 다시 만든다.
- unit, 대기, 결정, 승인, PR, 배포: 모두 같은 로그 위의 reducer.
- 중요한 것은 모델의 문맥에만 두지 않는다: `/clear` 해도 잃는 것이 없다.

---

## 6. 모델 없이 기다리기

```
stream 스크립트 (PR/CI, 세션, PC, 동기화, 막힘) ──바뀔 때 한 줄──> foreman (Monitor)
cron (인수인계, preflight, 접수) ─────────────────────────────────> foreman
```

- 지켜보는 건 스크립트다. 모델은 한 줄이 올 때만 깬다.
- lead 는 30분까지는 Monitor 로 기다리고, 더 긴 대기는 lead 를 멈췄다가 이벤트로 다시 띄운다.

---

## 7. 보드

- 사이드 패인(`/foundry-board`), 텍스트 보드, `board.html`.
- 상태별·저장소별 unit, 결정 카드, 승인 카드, 막힘, PR 재고,
  PC 부하, 아침 인수인계.
- 모든 버튼은 `foundry ... --by owner` 를 실행한다. 패인은 스스로 아무것도 쓰지 않는다.
- 화면 문구는 영어·일본어·한국어(`prefs ui.language`).

---

## 8. 밤과 아침

- 밤 시간대(기본 22:00–08:00): push 하지 않고, Slack 전송은 기다리고, toast 는 띄우지 않는다.
- 밤 작업은 dev 배포와 그 검증에서 멈춘다.
- 08:00 인수인계: 끝난 일, 열린 결정, 막힘, PR 변화, 비용.
- 조용한 시간을 푸는 건 owner 의 명시적인 "지금 보내" 뿐이고, 채널 하나에만 적용된다.

---

## 9. 안전

- 정리는 스스로 다시 확인한 것만 손댄다: 머지되거나 닫힌 PR, 깨끗한 worktree,
  push 안 된 것 없음, 안에서 도는 프로세스 없음. 나머지는 제안이 된다.
- 운영은 owner 의 몫이다: prod 배포에는 카드 위의 owner 지시가 필요하다.
- 머지는 리뷰한 head 커밋에 고정한다.

---

## 10. 설치와 시작

```sh
sh <marketplace>/foundry/install.sh
foundry init --repo app=~/src/app --tracker none
foundry foreman start        # foreman 이 이미 돌고 있으면 거부
claude attach foreman        # 첫 프롬프트: foundry up
```

`foundry foreman line` 은 손으로 시작할 때 쓰는 같은 시작 줄을 출력한다.

---

## 11. 현황과 한계

- 한 사람이 한 대의 기계에서 매일 쓴다. 다른 사람·다른 기계를 위해 일반화했다.
- 트래커: Jira, Linear, GitHub Issues, 또는 없음.
- journal 에 쓰인 글은 쓰인 언어 그대로 남는다.
- `--bg` 세션, Monitor, mod API 가 있는 Claude Code 가 필요하다.

알려진 한계는 `docs/oss-readiness.md` 를 본다.
