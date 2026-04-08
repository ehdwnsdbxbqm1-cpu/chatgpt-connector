# ask-gpt 시스템 아키텍처 상세 문서

> 이 문서는 ask-gpt 시스템의 전체 구조를 외부 리뷰어가 이해할 수 있도록 상세히 기술합니다.
> GPT에게 검토 및 개선 방법을 요청하기 위한 용도로 작성되었습니다.
> 최종 갱신: 2026-04-08

---

## 1. 시스템 개요

ask-gpt는 **웹 브라우저에 열려있는 ChatGPT(또는 다른 AI 서비스) 탭을 프로그래밍적으로 제어**하여, 백엔드 서버가 ChatGPT에 질문을 보내고 응답을 받아오는 시스템입니다.

**왜 API가 아닌 브라우저 자동화인가?**
- ChatGPT Plus/Team 구독자의 웹 인터페이스를 활용하여 API 비용 없이 GPT-4o/GPT-5 사용
- API로는 불가능한 GPT-5 thinking 모델 등 웹 전용 기능 활용
- NotebookLM 등 API가 없는 서비스도 동일한 패턴으로 연동 가능

**사용 목적:**
- 코드 리뷰 요청 (Claude Code에서 작성한 설계 스펙/코드를 GPT에 보내 크로스 리뷰)
- 멀티AI 브레인스토밍 (여러 AI에 동시 질문 → 교차 검토)
- 외부 AI 리뷰를 자동화된 워크플로우에 통합

---

## 2. 전체 아키텍처

```
┌──────────────────────────────────────────────────────────────────────┐
│  React Frontend (useAiReviewStream hook)                             │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │ POST /api/ai-review/stream → SSE event stream                  │ │
│  │ Events: init → phase* → complete/error                          │ │
│  │ Fallback: SSE 실패 시 POST /api/ai-review (3-case 분기)         │ │
│  └─────────────────────────────┬───────────────────────────────────┘ │
└────────────────────────────────┼─────────────────────────────────────┘
                                 │ HTTP
┌────────────────────────────────▼─────────────────────────────────────┐
│  FastAPI Backend                                                      │
│  ┌─────────────────┐  ┌──────────────────┐  ┌────────────────────┐  │
│  │ ai_review.py    │  │ ai_review_       │  │ phase_store.py     │  │
│  │ (Router)        │→ │ service.py       │→ │ (PhaseStore)       │  │
│  │                 │  │ (AiReviewService)│  │ in-memory pub/sub  │  │
│  │ POST /ai-review │  │                  │  │ terminal guard     │  │
│  │ POST /stream    │  │ provider lock    │  │ seq guard          │  │
│  │ SSE generator   │  │ deadline 관리    │  │ QueueFull tolerance│  │
│  └─────────────────┘  │ backoff retry    │  │ timeline logging   │  │
│                        └────────┬─────────┘  └────────────────────┘  │
│                                 │                                     │
│  ┌──────────────────────────────▼──────────────────────────────────┐ │
│  │ WebSocket Hub (ws/hub.py)                                       │ │
│  │ ┌──────────────────┐  ┌──────────────────────────────────────┐ │ │
│  │ │ ConnectionManager│  │ RequestRouter                        │ │ │
│  │ │ (연결 관리)       │  │ register → peek/resolve → cleanup   │ │ │
│  │ └──────────────────┘  │ FutureReplyTarget (단발 POST용)      │ │ │
│  │                        │ StreamReplyTarget (SSE용, phase CB) │ │ │
│  │                        └──────────────────────────────────────┘ │ │
│  └──────────────────────────────┬──────────────────────────────────┘ │
└─────────────────────────────────┼────────────────────────────────────┘
                                  │ WebSocket (ws://localhost:8000/ws)
┌─────────────────────────────────▼────────────────────────────────────┐
│  Chrome Extension (MV3 Service Worker)                                │
│  ┌──────────────────────────────────────────────────────────────────┐│
│  │ service-worker.js                                                ││
│  │ - WsManager: WebSocket 연결 관리 + JWT 인증                     ││
│  │ - RequestRouter: 요청-응답 매핑                                  ││
│  │ - handleAiChat: 탭 확보 → bridge에 메시지 전달                   ││
│  │ - handleAiRead: 탭에서 마지막 응답 읽기                          ││
│  │ - ai_phase 중계: bridge → WS Hub → SSE client                   ││
│  │ - TAB_RECOVERY: 탭 복구 시 자동 phase emit                      ││
│  │ - ensureTabReady: recovery-on-demand + health-check alarm        ││
│  └───────────────────────────┬──────────────────────────────────────┘│
│                              │ chrome.tabs.sendMessage                │
│  ┌───────────────────────────▼──────────────────────────────────────┐│
│  │ bridge-script.js (Content Script — ChatGPT 페이지에 주입)        ││
│  │                                                                   ││
│  │ [질문 전송 흐름]                                                  ││
│  │ 1. submitQuestion: adapter-config.json에서 셀렉터 로드            ││
│  │ 2. newChat 클릭 → SPA 네비게이션 대기 (DOM 폴링)                 ││
│  │ 3. typeAndSubmit: inputSelector로 입력란 찾기 → 텍스트 입력 → 전송││
│  │ 4. emitPhase("SUBMITTING", "submitted")                          ││
│  │                                                                   ││
│  │ [응답 관찰 흐름 — observeResponse 상태 머신]                     ││
│  │ WAITING_START → GENERATING → STABILIZING → resolve(fullText)     ││
│  │                                                                   ││
│  │ [Phase Emission]                                                  ││
│  │ emitPhase(phase, detail) → SW → WS Hub → PhaseStore → SSE       ││
│  │ phaseSeq: 요청당 monotonic 카운터 (역전 방어)                    ││
│  └──────────────────────────────────────────────────────────────────┘│
│                                                                       │
│  adapter-config.json: provider별 DOM 셀렉터 정의                     │
│  { "chatgpt": { hosts, inputSelector[], responseSelector[],           │
│     busyIndicator[], newChatSelector[], submitSelector[],             │
│     loginIndicator, useInputAwareLoginDetection } }                   │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 3. 요청 생명주기 (Happy Path)

### 3.1 SSE 스트리밍 경로 (권장)

```
사용자 클릭 "GPT 리뷰"
  → Frontend: POST /api/ai-review/stream { provider:"chatgpt", context, question, task_class:"quick_review" }
  → Router: asyncio.create_task(request_review_stream)
  → Service: provider lock 확인 → _active_providers["chatgpt"] = request_id
  → Service: PhaseStore.create(request_id, prompt_hash, "chatgpt")
  → Service: PhaseStore.update_phase("QUEUED")
  → Router: PhaseStore에서 request_id 탐색 → subscribe → SSE "init" 전송
  → Service: Hub.send_to_extension_stream(message, phase_callback)
    → Hub: StreamReplyTarget(future, phase_callback) 등록
    → Hub: WebSocket으로 extension에 ai_chat 메시지 전송
  → Extension SW: handleAiChat → ensureAiTab → tabs.sendMessage(bridge)
    → SW: TAB_RECOVERY 필요 시 ai_phase("TAB_RECOVERY") emit
  → Bridge: submitQuestion → typeAndSubmit
    → emitPhase("SUBMITTING") → SW → WS → Hub → StreamReplyTarget.phase_callback
    → PhaseStore.update_phase("SUBMITTING") → subscriber queue → SSE "phase"
  → Bridge: observeResponse 상태 머신 시작
    → emitPhase("WAITING_START") → ... → emitPhase("GENERATING") → ... → emitPhase("STABILIZING")
    → 각 phase가 SSE로 실시간 스트리밍
  → Bridge: resolve(fullText) → ai_response_done → SW → WS ai_done
  → Hub: StreamReplyTarget.future.set_result(response)
  → Service: response 수신 → prompt_hash mismatch 검사 → PhaseStore.update_phase("COMPLETED")
  → Service: return { status:"success", response, elapsed_ms, prompt_hash, ... }
  → Router: SSE "complete" { response, elapsed_ms, meta } 전송
  → Frontend: setResponse(data.response), setPhase("COMPLETED")
```

### 3.2 일반 POST 경로 (fallback)

```
POST /api/ai-review { provider:"chatgpt", action:"ask", context, question }
  → Service.request_review: provider lock → Hub.send_to_extension → FutureReplyTarget
  → Extension 처리 동일 (phase는 전송되지만 PhaseStore 미연동)
  → response 수신 → 즉시 JSON 반환
```

---

## 4. 에러 처리 및 복구 전략

### 4.1 에러 코드 체계 (13개)

| 코드 | 복구 가능 | 원인 | retry_hint |
|------|----------|------|-----------|
| EXTENSION_OFFLINE | X | 크롬 확장 WebSocket 미연결 | check_extension |
| TAB_OPEN_FAILED | O | ChatGPT 탭 열기 실패 | retry |
| TAB_NOT_READY | O | 탭 로딩 중 | retry |
| TAB_NOT_FOUND | O | 탭 상태 비정상 | retry |
| LOGIN_REQUIRED | X | ChatGPT 로그인 필요 | relogin |
| AUTH_EXPIRED | X | AI 세션 만료 | relogin |
| PROVIDER_BUSY | O | 이전 요청 처리 중 (1 concurrent/provider) | wait_and_retry |
| CAPTURE_TIMEOUT | O | 응답 대기 시간 초과 | increase_timeout |
| EMPTY_AFTER_COMPLETE | O | 생성 완료되었으나 텍스트 비어있음 (DOM 지연) | wait_and_read |
| CAPTURE_FAILED | O | 캡처 일반 실패 | wait_and_read |
| STILL_GENERATING | O | AI가 아직 생성 중 | wait_and_read |
| DOM_SELECTOR_MISS | X | DOM 구조 변경됨 | update_selector |
| NOTEBOOK_NOT_OPENED | X | NotebookLM 노트북 미열림 | open_notebook |

### 4.2 Timeout 정책 분리 (task_class / model / override)

`ai_review_service.py`의 deadline 관리를 프론트 요청 파라미터(`timeout`, `model`)와 분리하고, 서버가 timeout 정책을 강제한다.

#### 4.2.1 요청 계약

프론트는 임의 timeout 대신 `task_class` 중심으로 요청한다.

```json
{
  "provider": "chatgpt",
  "task_class": "quick_review",
  "context": "...",
  "question": "...",
  "timeout_override_sec": 360
}
```

`timeout_override_sec`는 선택값이며 서버 허용 범위 밖이면 clamp 또는 무시한다.

#### 4.2.2 서버 강제 정책 (예시)

| task_class | 기본 timeout | 최대 허용 timeout |
|------------|--------------|-------------------|
| quick_review | 300s | 420s |
| deep_reasoning | 420s | 600s |

모델 보정 규칙:
- GPT-5 계열(`model.startswith("gpt-5")`)은 기본 timeout +180s (예: 300→480s)
- 단, task_class 상한선을 초과하지 않도록 clamp
- 사용자 override 허용 범위는 class별 `[min_override, max_override]`로 제한

deadline 계산 순서:
1. `base = class_default(task_class)`
2. `base = apply_model_bonus(base, model)`
3. `effective_default = min(base, class_max(task_class))`
4. override가 있으면 `effective = clamp(override, min_override, class_max)` 아니면 `effective_default`

### 4.2 Backoff Retry 메커니즘

CAPTURE_FAILED 또는 CAPTURE_TIMEOUT 발생 시 자동 read 재시도:

```
delays = [2, 4, 8, 16, 16, 16]초 (exponential backoff)

각 retry:
  1. deadline까지 남은 시간 확인 (< 5초면 포기)
  2. delay만큼 대기
  3. read_last_response(provider) 호출
  4. 결과에 따라:
     - success → 반환 (recovered_via: "backoff_retry")
     - STILL_GENERATING → continue (다음 retry)
     - EMPTY_AFTER_COMPLETE → 전용 처리 (아래)
     - LOGIN_REQUIRED/EXTENSION_OFFLINE/AUTH_EXPIRED → 즉시 반환 (복구 불가)

EMPTY_AFTER_COMPLETE 전용 처리:
  - 최대 2회 quick retry (1초 → 2초 간격)
  - 2회 실패 시 즉시 lock 해제 + 에러 반환
  - 목적: 300초 lock 고착 방지 (DOM 렌더링 지연은 보통 0.5~3초)
```

### 4.3 Soft Extension (PhaseStore 진행 신호 기반)

장시간 추론 모델의 hard timeout 직전 종료를 완화하기 위해 soft extension을 1회 허용한다.

`PhaseStore`는 각 요청마다 `last_progress_at`(마지막 진행 phase 시각)과 `soft_extend_used`를 관리한다.

soft extension 트리거 조건(예시):
1. 현재 시각이 hard deadline 근접 (`now >= deadline - 30s`)
2. 최근 진행 신호 존재 (`now - last_progress_at <= 20s`)
3. `soft_extend_used == false`

조건 만족 시:
- hard deadline을 1회만 +90s 연장
- `soft_extend_used = true` 설정
- timeline에 `SOFT_EXTENDED` 이벤트 기록

### 4.4 Frontend SSE→POST Fallback (3-case + 수동 read)

```
SSE 스트림 실패 시 마지막 수신 phase에 따라 분기:

Case A (submit 실패): lastPhase = null 또는 "QUEUED"
  → POST /api/ai-review { action: "ask", ...원본 파라미터 } (전체 재시도)

Case B (생성 중 끊김): lastPhase = "THINKING" 또는 "GENERATING"
  → POST /api/ai-review { action: "read", timeout: 10 } (즉시 read)

Case C (그 외): lastPhase = "SUBMITTING", "WAITING_START", "STABILIZING" 등
  → 3초 대기 → POST /api/ai-review { action: "read", timeout: 10 }

UI/UX 보강:
- `GENERATING`/`THINKING` 장기 유지 시 “장시간 추론 중” 상태 배지 노출
- 예상 대기 가이드(예: “최대 8분 소요 가능”) 명시
- 사용자가 수동으로 `read` fallback을 즉시 트리거할 수 있는 버튼 제공
```

### 4.5 탭 복구 체계 (Recovery-on-Demand)

```
ask/read 요청 수신
  → ensureAiTab: ping 실패?
    → ensureTabReady(forceReload): reload → waitForTabReady → executeScript → backoff ping
      → 성공: { tabId, recovered: true }
      → 8회 ping 실패: TAB_RECOVERY_FAILED

- ask: forceReload: true (탭 완전 리로드)
- read: forceReload: false (기존 응답 보존)
- recovery 시 ai_phase("TAB_RECOVERY") emit → SSE로 프론트에 실시간 표시
```

---

## 5. PhaseStore — 실시간 상태 추적

### 5.1 구조

```python
PhaseRecord:
  request_id: str        # UUID
  prompt_hash: str       # SHA-256(context+question)[:8]
  provider: str          # "chatgpt" | "notebooklm"
  current_phase: str     # 현재 phase
  timeline: list[dict]   # [{phase, elapsed_ms, ts, detail?}, ...]
  start_time: float      # monotonic clock
  seq: int               # 내부 순차 카운터 (역전 방어)
  subscribers: list[Queue]  # SSE generator가 구독하는 asyncio.Queue (maxsize=50)
  completed_at: float?   # terminal phase 도달 시 monotonic clock
  last_progress_at: float?  # 최근 진행 phase 시각
  soft_extend_used: bool    # soft extension 1회 사용 여부

PhaseStore:
  _records: dict[request_id → PhaseRecord]
  _miss_counts: dict[id(queue) → consecutive_miss_count]
  default_ttl: 300초 (완료 후 자동 정리)
```

### 5.2 Phase 흐름

```
QUEUED → [TAB_RECOVERY] → SUBMITTING → WAITING_START → GENERATING → STABILIZING → COMPLETED
                                                                                  → FAILED
                                                                                  → CANCELLED
```

9개 Phase. bridge-script에서 emitPhase()로 발생, SW가 WS로 중계, Hub가 StreamReplyTarget.phase_callback 호출, PhaseStore에 기록.

진행 phase(`SUBMITTING`, `WAITING_START`, `GENERATING`, `STABILIZING`) 수신 시 `last_progress_at`을 갱신한다.

### 5.3 방어 Guard (3개)

**Terminal Guard**: `completed_at is not None`이면 모든 후속 update_phase 호출을 warning 로그 남기고 무시. COMPLETED/FAILED/CANCELLED 후 추가 이벤트가 도착해도 상태 오염 없음. 동일 terminal phase 중복 호출도 차단 (duplicate complete/error 방지).

**Seq Guard**: `incoming_seq`가 제공되고 `incoming_seq <= record.seq`이면 무시. bridge-script가 요청마다 리셋되는 monotonic phaseSeq를 보내고, 백엔드에서 역전 차단. incoming_seq가 None이면 (레거시 호출자) 항상 통과.

**QueueFull Tolerance**: subscriber queue에 push 실패(QueueFull) 시 즉시 제거하지 않고 `_miss_counts` dict로 3회 연속 실패 시에만 제거. 느린 SSE 소비자 보호. push 성공 시 카운트 리셋.

### 5.4 Late Subscriber Replay Contract

- subscribe() 시 현재 current_phase를 즉시 1개 이벤트로 push
- 전체 timeline은 replay하지 않음
- terminal phase 이후 subscribe해도 마지막 상태 1개는 수신됨

### 5.5 구조화 로그 (phase_timeline)

terminal phase 도달 시 `vidmaker.phase_timeline` 로거로 JSON 출력:

```json
{
  "version": 1,
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "prompt_hash": "abcd1234",
  "provider": "chatgpt",
  "status": "COMPLETED",
  "total_ms": 15230,
  "phase_count": 7,
  "subscriber_count": 1,
  "phases": [
    {"phase": "QUEUED", "elapsed_ms": 0, "ts": "2026-04-08T10:30:00+00:00"},
    {"phase": "SUBMITTING", "elapsed_ms": 1200, "ts": "..."},
    {"phase": "WAITING_START", "elapsed_ms": 2400, "ts": "..."},
    {"phase": "GENERATING", "elapsed_ms": 5000, "ts": "..."},
    {"phase": "STABILIZING", "elapsed_ms": 13000, "ts": "..."},
    {"phase": "COMPLETED", "elapsed_ms": 15230, "ts": "..."}
  ],
  "ts": "2026-04-08T10:30:15+00:00"
}
```

RotatingFileHandler: `logs/phase_timeline.log`, 10MB, 3 backup.

---

## 6. 운영 지표 및 timeout 재보정

timeout 기본값은 고정값이 아니라 운영 지표 기반으로 주기적 재보정을 수행한다.

모델별 필수 지표:
- 완료 시간 분포: p50 / p95 / p99
- timeout 비율: timeout_count / total_requests
- soft extension 발동률 및 연장 후 완료 성공률

권장 루프(주 1회):
1. 최근 7일 데이터를 모델/태스크 클래스별 집계
2. p95/p99와 timeout 비율을 기준으로 class 기본 timeout/상한선 조정
3. GPT-5 bonus(+180s) 적정성 검토
4. canary 적용 후 재측정

---

## 7. SSE 스트리밍 프로토콜

### 6.1 엔드포인트

```
POST /api/ai-review/stream
Content-Type: application/json
Authorization: Bearer <jwt>
Body: { provider, action:"ask", context, question, task_class, timeout_override_sec?, new_chat }

Response: Content-Type: text/event-stream
```

### 6.2 SSE 이벤트 타입

| Event | 데이터 | 설명 |
|-------|--------|------|
| `init` | `{request_id, prompt_hash, seq:0}` | 요청 시작, correlation ID 발급 |
| `phase` | `{phase, elapsed_ms, seq, detail}` | 실시간 phase 변경 |
| `heartbeat` | `{seq, elapsed_ms, remaining_ms, stale}` | 30초 간격 keepalive |
| `complete` | `{phase:"COMPLETED", response, elapsed_ms, seq, meta}` | 성공 완료 |
| `error` | `{phase:"FAILED", error_code, message, recoverable, retry_hint, elapsed_ms, seq}` | 실패 |
| `warning` | `{type:"HASH_MISMATCH", prompt_hash, seq}` | prompt_hash 불일치 경고 |

### 6.3 SSE Generator 내부 동작

```python
async def generate():
    task = asyncio.create_task(request_review_stream(...))
    # 50ms 대기 후 PhaseStore에서 request_id 탐색
    queue = PhaseStore.subscribe(request_id)
    yield "init"

    while not task.done():
        event = await wait_for(queue.get(), timeout=30)  # heartbeat 간격
        if terminal phase: break
        yield "phase"
        # 30초 무응답 시 heartbeat (remaining_ms 포함)
        # deadline 초과 시 break

    result = await task
    yield "complete" or "error"
    if prompt_hash_mismatch: yield "warning"

    # finally: unsubscribe + task.cancel() + await CancelledError
```

---

## 8. WebSocket Hub 레이어

### 7.1 역할

백엔드 서비스와 크롬 확장 사이의 **메시지 중계**. JWT 인증, role 기반 인가, 요청-응답 매핑.

- 연결 타입: extension(1개만), frontend(다수), mcp
- 인증: 5초 내 JWT auth 메시지 필수
- heartbeat: ping → pong 즉시 응답

### 7.2 메시지 타입

| 방향 | 타입 | 설명 |
|------|------|------|
| Backend → Extension | `ai_chat` | AI에 질문 전송 요청 |
| Backend → Extension | `ai_read` | 마지막 응답 읽기 요청 |
| Extension → Backend | `ai_chunk` | 응답 일부 (스트리밍, peek으로 라우팅) |
| Extension → Backend | `ai_phase` | phase 변경 이벤트 (peek으로 라우팅) |
| Extension → Backend | `ai_done` | 응답 완료 + 전체 텍스트 (resolve로 라우팅) |
| Extension → Backend | `ai_read_result` | read 결과 (resolve로 라우팅) |
| Extension → Backend | `error` | 에러 응답 (resolve로 라우팅) |

peek = 항목 유지 (스트리밍용), resolve = 항목 제거 (최종 응답).

### 7.3 ReplyTarget 패턴

```python
class FutureReplyTarget(ReplyTarget):
    # 일반 POST 요청용. ai_done/error → future.set_result(). chunk 무시.

class StreamReplyTarget(ReplyTarget):
    # SSE 스트리밍용. ai_phase → phase_callback(msg). ai_done/error → future.set_result().

class WebSocketReplyTarget(ReplyTarget):
    # 프론트엔드 WS 클라이언트에서 직접 ai_chat 보냈을 때. 메시지를 WebSocket으로 전달.
```

### 7.4 내부 호출 API

```python
# 단발 요청 (POST /ai-review에서 사용)
Hub.send_to_extension(message, timeout) → dict
  # FutureReplyTarget 사용, asyncio.Future 대기

# 스트리밍 요청 (POST /api/ai-review/stream에서 사용)
Hub.send_to_extension_stream(message, timeout, phase_callback) → dict
  # StreamReplyTarget 사용, phase 이벤트 콜백 + asyncio.Future 대기
```

### 7.5 Extension Disconnect 처리

- Extension 연결 끊김 → cleanup_by_target로 모든 pending 요청에 EXTENSION_NOT_CONNECTED 전파
- 새 Extension 연결 시 이전 연결 강제 교체 → 동일 cleanup

### 7.6 정리 주기

- 10초 간격 cleanup loop: 타임아웃 초과 pending 요청 제거 + EXTENSION_TIMEOUT 에러 전파
- 요청별 커스텀 타임아웃 지원 (없으면 글로벌 60초)

---

## 9. Chrome Extension 레이어

### 9.1 Service Worker (background/service-worker.js)

**MV3 제약**: Service Worker는 30초 idle 후 종료됨. WebSocket 연결이 끊길 수 있음.

**WsManager**: WebSocket 연결 관리. 자동 재연결 (exponential backoff). JWT 인증.

**주요 핸들러**:
- `handleAiChat(msg)`: ensureAiTab(target) → 실패 시 ensureTabReady(forceReload:true) + TAB_RECOVERY emit → tabs.sendMessage(bridge, { action:"ai_chat", deadline_ms })
- `handleAiRead(msg)`: ensureAiTab(target) → 실패 시 ensureTabReady(forceReload:false) → tabs.sendMessage(bridge, { action:"read_last_response" })
- `ai_phase 중계`: bridge chrome.runtime.sendMessage → ws.send({ type:"ai_phase", data: { phase, elapsed_ms, detail } })
- `ai_response_done 중계`: bridge → ws.send({ type:"ai_done", data: { full_text } })
- `ai_read_result 중계`: bridge read_result → ws.send({ type:"ai_read_result", data: { full_text, busy } })

**Tab Utils**:
| 함수 | 역할 | 파라미터 |
|------|------|---------|
| `ensureAiTab(target, purpose)` | 기존 탭 확인, 없으면 열기, ping health-check | purpose별 탭 격리 |
| `ensureTabReady(target, {forceReload})` | reload → waitForTabReady → executeScript → backoff ping | forceReload: ask=true, read=false |
| `waitForTabReady(tabId, timeout)` | tab status=complete 대기 | 15초 타임아웃 |

**Alarm 기반 작업**:
| Alarm | 주기 | 동작 |
|-------|------|------|
| ws-keepalive | 30초 | WS ping 전송 |
| health-check | 1분 | AI 탭 스캔 + WS 재연결 확인 |
| router-cleanup | 5분 | 오래된 pending 요청 정리 |

### 9.2 Bridge Script (ai/bridge-script.js)

**주입**: manifest content_scripts로 ChatGPT/NotebookLM 도메인에 자동 주입. ISOLATED world.

**Idempotent Guard**: `window.__AI_BRIDGE_INJECTED__`로 중복 주입 방지. 탭 새로고침 시 리셋.

**핵심 핸들러 3개**:

| 메시지 | 동작 | 응답 |
|--------|------|------|
| `ping` | adapter-config 로드 → input/busy/login 상태 체크 | `pong { adapterReady, busy, loginRequired }` |
| `ai_chat` | submitQuestion → observeResponse | `ai_response_done { fullText }` 또는 에러 |
| `read_last_response` | 미니 폴러로 마지막 응답 텍스트 추출 | `read_result { success, text, busy }` |

**submitQuestion 흐름**:
1. adapter-config.json 로드 (target별 셀렉터)
2. newChat=true이면 newChatSelector 클릭 → SPA 네비게이션 대기 (최대 8초, DOM 폴링)
3. beforeCount = 현재 responseSelector 매칭 수
4. typeAndSubmit: inputSelector → 텍스트 입력 → submitSelector 클릭
5. emitPhase("SUBMITTING", "submitted")

**observeResponse 상태 머신**:
```
           ┌──────────────────┐
           │  WAITING_START   │
           │ (응답 대기 중)    │
           └───┬──────────┬───┘
    isBusy     │          │ messages > beforeCount
    detected   │          │ + text 있음
               ▼          ▼
        ┌──────────┐  ┌──────────────┐
        │GENERATING│  │ STABILIZING  │
        │(스트리밍) │  │(안정화 확인)  │
        └──┬───┬───┘  └──┬───────┬───┘
           │   │         │       │
   !isBusy │   │ isBusy  │       │ stableCount
   + text  │   │ 재등장   │       │ >= target
           ▼   └────────►│       ▼
     STABILIZING ◄───────┘   COMPLETE
                              (resolve)

모든 상태에서: remaining < 5초 → CAPTURE_TIMEOUT (reject)
GENERATING에서: messages.length < beforeCount → beforeCount 리셋 (SPA 네비게이션 감지)
```

| 파라미터 | 값 |
|----------|-----|
| 폴링 간격 | 800ms |
| Stable checks (busy 있음) | 3회 연속 |
| Stable checks (busy 없음) | 5회 연속 |
| Safety margin | 5,000ms (deadline 5초 전 중단) |
| Extension cap | 600,000ms (10분 하드캡) |

**read_last_response 미니 폴러**:
- 800ms 간격, 최대 12회 (~10초)
- stable 판정: 텍스트 2회 연속 동일 → 반환
- busyIndicator 체크: busy=true → 아직 생성 중 (백엔드에서 STILL_GENERATING)
- 실패 시 diagnostics 포함: `{ articleCount, lastArticlePreview, url }`

**emitPhase**:
```javascript
let phaseSeq = 0;  // 요청마다 리셋
function emitPhase(phase, detail = null) {
    phaseSeq++;
    chrome.runtime.sendMessage({
        action: "ai_phase",
        requestId: currentRequestId,
        phase: phase,
        elapsed_ms: Date.now() - requestStartTime,
        detail: detail,
        seq: phaseSeq,  // 백엔드 seq guard용
    });
}
```

### 9.3 adapter-config.json

```json
{
  "chatgpt": {
    "hosts": ["chat.openai.com", "chatgpt.com"],
    "inputSelector": ["#prompt-textarea", "div[contenteditable='true'][id='prompt-textarea']"],
    "submitSelector": ["button[data-testid='send-button']", "button[aria-label='Send prompt']"],
    "responseSelector": ["div[data-message-author-role='assistant']", "article[data-testid]"],
    "busyIndicator": ["button[aria-label='Stop generating']", "button[aria-label='Stop']"],
    "newChatSelector": ["a[data-testid='create-new-chat-button']", "a[href='/']"],
    "loginIndicator": "button[data-testid='login-button']",
    "useInputAwareLoginDetection": false
  }
}
```

각 셀렉터는 배열로 fallback 지원: `queryFirst(selectors)` / `queryAll(selectors)`가 순서대로 시도.

---

## 9. 동시성 제어

### 9.1 Provider Lock

```python
_active_providers: dict[str, str]  # provider → request_id
_state_lock: asyncio.Lock          # race condition 방지

# provider당 1개 동시 요청만 허용
# PROVIDER_BUSY 에러로 즉시 거절 (대기열 없음)
# finally에서 항상 해제 (CancelledError 포함)
# request_review_stream은 async with _state_lock으로 lock/unlock 보호
```

### 9.2 EMPTY_AFTER_COMPLETE Lock 고착 방지

이전 문제: read 실패(EMPTY_AFTER_COMPLETE) 시 backoff가 deadline(300초)까지 lock을 유지 → 다른 요청 차단.
현재: EMPTY_AFTER_COMPLETE는 최대 2회 quick retry(1s+2s=3초) 후 즉시 lock 해제 + 에러 반환.

### 9.3 SSE Disconnect Cleanup

SSE generator의 finally에서:
1. PhaseStore.unsubscribe (subscriber queue 제거)
2. task.cancel() + await CancelledError
3. CancelledError가 request_review_stream의 finally를 실행 → _active_providers.pop() → lock 해제

---

## 10. prompt_hash 검증

```
요청 시: prompt_hash = SHA-256(context + question)[:8]
전송 시: message.payload.prompt_hash로 bridge에 전달
응답 시: bridge가 response.data.prompt_hash로 반환
검증 시: response_hash != prompt_hash이면:
  - warning 로그 출력
  - result에 prompt_hash_mismatch=true 플래그
  - SSE "warning" { type:"HASH_MISMATCH" } 이벤트 전송
```

목적: 다른 사용자/세션의 응답을 잘못 읽는 상황 감지.

---

## 11. Dev Auto-Reload

```
tools/dev-watch.js (Node)
  → chokidar 감시: bridge-script.js, service-worker.js, tab-utils.js 등
  → 파일 변경 → 300ms debounce → WS(17171) DEV_RELOAD_EXTENSION 전송

service-worker.js (Extension)
  → connectDevWs(): development 모드에서만 연결 (chrome.management.getSelf)
  → DEV_RELOAD_EXTENSION 수신 → storage에 reload intent 저장 → runtime.reload()
  → 새 SW: checkDevReloadPending() → 60초 TTL 확인 → reloadAiTabsAndReinject()
  → 각 AI 탭: reload → status=complete 대기 → executeScript(bridge-script.js)
```

---

## 12. 코드 파일 목록 및 크기

| 파일 | 줄 수 | 역할 |
|------|------|------|
| `modules/vidmaker/services/ai_review_service.py` | ~400 | 핵심 서비스: provider lock, backoff retry, hash 검증 |
| `modules/vidmaker/services/phase_store.py` | ~166 | PhaseStore: in-memory pub/sub, guard, logging |
| `modules/vidmaker/routers/ai_review.py` | ~145 | REST 라우터 + SSE generator |
| `modules/vidmaker/services/ws/hub.py` | ~350 | WebSocket Hub: 메시지 라우팅, 내부 호출 API |
| `modules/vidmaker/services/ws/request_router.py` | ~225 | 요청-응답 매핑, ReplyTarget 추상화 |
| `modules/vidmaker/services/ws/connection_manager.py` | ~150 | WebSocket 연결 관리 |
| `chrome-extension/background/service-worker.js` | ~400 | MV3 Service Worker: WS 관리, 탭 관리, 메시지 중계 |
| `chrome-extension/ai/bridge-script.js` | ~420 | Content Script: DOM 자동화, 상태 머신 |
| `chrome-extension/ai/adapter-config.json` | ~50 | Provider별 DOM 셀렉터 |
| `chrome-extension/background/tab-utils.js` | ~200 | 탭 확보/복구 유틸리티 |
| `chrome-extension/background/ws-manager.js` | ~150 | WebSocket 클라이언트 + 자동 재연결 |
| `chrome-extension/background/request-router.js` | ~80 | Extension 측 요청-응답 매핑 |
| `chrome-extension/tools/dev-watch.js` | ~60 | 개발용 파일 감시 + 리로드 |
| `frontend/src/hooks/useAiReviewStream.js` | ~123 | React Hook: SSE 구독 + 3-case fallback |
| `frontend/src/utils/sseClient.js` | ~57 | POST 기반 SSE async generator 파서 |

---

## 13. 테스트 현황

| 영역 | 파일 | 테스트 수 | 커버리지 |
|------|------|----------|---------|
| PhaseStore | test_phase_store.py | 13 | terminal/seq/QueueFull guard, CRUD, subscribe, log |
| SSE Endpoint | test_ai_review_stream.py | 2 | init event, disconnect cancel |
| prompt_hash | test_prompt_hash.py | 4 | deterministic, different inputs, none, empty |
| StreamReplyTarget | test_stream_reply_target.py | 4 | chunk ignore, phase route, done resolve, error resolve |
| Bridge phase | bridge-phase.test.js | 5 | emitPhase 전체 흐름 |
| SW phase relay | sw-phase-relay.test.js | 2 | ai_phase WS 중계 |
| **합계** | **6파일** | **30** | |

**미커버 영역 (HIGH)**:
- backoff retry 전체 흐름 (EMPTY_AFTER_COMPLETE 포함)
- Hub 내부 호출 (send_to_extension, send_to_extension_stream)
- adapter-config 셀렉터 실제 검증
- 탭 복구 E2E 흐름 (ensureTabReady → bridge reinject)
- Frontend fallback 3-case 동작

---

## 14. 알려진 이슈 및 한계

### 14.1 DOM 셀렉터 불안정성 (심각도: HIGH)
- ChatGPT는 수시로 UI를 변경함. responseSelector, inputSelector 등이 깨질 수 있음.
- adapter-config.json에 fallback 셀렉터 배열을 사용하지만 근본적으로 취약.
- read 실패 시 diagnostics(articleCount, lastArticlePreview, url)를 로그에 남기도록 개선됨.
- 현재 셀렉터 업데이트는 수동 (ChatGPT 페이지를 직접 확인해야 함).

### 14.2 MV3 Service Worker 수명 (심각도: HIGH)
- Chrome MV3는 Service Worker를 30초 idle 후 종료.
- WebSocket 연결이 끊기면 EXTENSION_OFFLINE 에러 발생.
- health-check alarm(1분 간격)과 자동 재연결이 있지만 최대 60초 갭.
- 사용자에게 "확장 재시작" 요청이 가끔 발생.

### 14.3 GPT-5 Thinking 모델 타임아웃 (심각도: MEDIUM)
- GPT-5 thinking 모델은 60~300초+ 사고 시간.
- deadline 최대 600초 설정 가능하지만 그래도 timeout 빈발.
- 근본적 한계 — 타임아웃 늘리는 것 외에 방법 없음.

### 14.4 단일 Extension 제약 (심각도: LOW)
- Hub에 extension 역할 연결은 1개만 허용 (교체 시 이전 연결 강제 종료).
- 브라우저 1개 = 시스템 1개.

### 14.5 로그인 상태 의존 (심각도: MEDIUM)
- ChatGPT/NotebookLM 웹 세션이 만료되면 LOGIN_REQUIRED 에러.
- 자동 로그인 불가 — 사용자가 수동으로 브라우저에서 로그인해야 함.

---

## 15. 개선 검토 요청 사항

이 시스템의 구조와 코드를 검토하고, 다음 관점에서 개선 방법을 제안해주세요:

1. **아키텍처 개선**: 레이어 간 결합도, 메시지 전달 경로의 복잡성, 더 나은 추상화 방법
2. **안정성 강화**: DOM 셀렉터 취약성 근본 해결, MV3 SW 수명 문제, WebSocket 재연결 전략
3. **에러 복구**: backoff retry 전략의 적절성, fallback 케이스 누락 여부, 에러 전파 경로의 완전성
4. **동시성**: provider lock 전략 개선, 대기열 도입 여부, single-extension 제약 완화
5. **관찰성(Observability)**: phase 추적의 완성도, 로그/모니터링 개선, 디버깅 용이성
6. **테스트**: 커버리지 우선순위, 통합 테스트 전략, DOM 자동화 테스트 방법
7. **확장성**: 새 AI provider 추가 시 변경 범위, adapter 패턴의 확장성
8. **보안**: WebSocket 인증 강도, 프롬프트 데이터 노출 위험, extension 권한 최소화
