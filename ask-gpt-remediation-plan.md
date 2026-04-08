# ask-gpt 장애 대응/개선 작업안

기준 문서: `ask-gpt-architecture.md` (최종 갱신 2026-04-08)

---

## 범위
아래 3가지 이슈에 대해 원인/대응 방향을 실행 가능한 작업 항목으로 정리한다.

- DOM_SELECTOR_MISS ("답변 못읽었다")
- EXTENSION_OFFLINE ("확장 재시작")
- GPT-5 장시간 thinking 타임아웃

---

## 1) DOM_SELECTOR_MISS 대응

### 목표
ChatGPT DOM 변경 시, 어떤 selector가 어디서 실패했는지 재현 가능한 진단 정보를 확보한다.

### 작업 항목
1. `bridge-script.js`에 selector 진단 공통 로깅 유틸 추가.
2. selector 그룹(`input/response/newChat/submit/busyIndicator`)과 index, `matched_count`, `visible_count`, `host`, `pathname`, `request_id`, `phase`를 구조화해 기록.
3. 최종 `DOM_SELECTOR_MISS` payload에 “시도한 selector 목록”과 “마지막 성공 selector(존재 시)” 포함.
4. SW → WS Hub 전달 중 payload 손실 없이 백엔드까지 보존.
5. 로그에 프롬프트 본문/개인정보가 남지 않도록 마스킹 규칙 문서화.

### 완료 기준
- DOM miss 재발 시 단일 로그 이벤트만으로 실패 selector와 실패 phase를 확인할 수 있어야 함.

---

## 2) EXTENSION_OFFLINE 대응 (MV3 SW Idle)

### 목표
Service Worker idle 종료가 발생해도 자동 복구되며, 사용자에게 즉시 실패를 반환하지 않도록 한다.

### 작업 항목
1. `service-worker.js` 기동 시 즉시 WS connect/register 수행.
2. `onclose`에 지수 백오프 재연결(예: 1/2/4/8초, 최대 30초) 추가.
3. 백엔드 `ws/hub.py`에 reconnect grace window를 도입해 단절 직후 요청을 짧게 유예.
4. grace window 내 재연결 성공 시 요청 재전송.
5. 재연결 실패 + 유예 초과 시에만 `EXTENSION_OFFLINE` 확정.
6. 재연결 성공 이벤트(`EXTENSION_RECONNECTED`)를 phase로 emit해 프론트 상태 표시.

### 완료 기준
- SW 재시작/idle 종료 이후에도 수동 확장 재시작 없이 일정 비율 이상 자동 복구.

---

## 3) GPT-5 장시간 Thinking 타임아웃 대응

### 목표
모델 특성(장시간 추론)을 고려한 timeout 정책으로 불필요한 실패를 줄인다.

### 작업 항목
1. 요청 파라미터에 `task_class`(예: `quick_review`, `deep_reasoning`) 도입.
2. `task_class`/모델 조합별 서버 기본 timeout 정책 테이블 정의.
3. GPT-5 계열 기본 timeout 상향(예: 300초 → 480초), 상한선과 override 범위 명시.
4. `PhaseStore`에 마지막 진전 시각을 저장해, 진전이 관측되면 soft extension 1회 허용.
5. 프론트에 장시간 추론 안내 UI와 수동 `read` fallback 트리거 노출.
6. 운영 지표(p50/p95/p99, timeout율) 기반 분기별 timeout 재보정.

### 완료 기준
- GPT-5 관련 timeout 실패율 유의미 감소.
- 사용자에게 “실패”보다 “추론 중/복구 중” 상태를 우선 제공.

---

## 권장 우선순위
1. DOM_SELECTOR_MISS 진단 로그 표준화 (즉시)
2. EXTENSION_OFFLINE 재연결/유예 재전송 (다음 스프린트)
3. GPT-5 timeout 정책 분리 (제품 정책 반영)

