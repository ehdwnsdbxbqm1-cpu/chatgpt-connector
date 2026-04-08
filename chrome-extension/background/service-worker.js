const WS_BACKOFF_STEPS_MS = [1000, 2000, 4000, 8000, 16000, 30000];
const WS_BACKOFF_CAP_MS = 30000;

function nowMs() {
  return Date.now();
}

class WsManager {
  constructor({ wsUrl, tokenProvider, requestRouter, phaseEmitter }) {
    this.wsUrl = wsUrl;
    this.tokenProvider = tokenProvider;
    this.requestRouter = requestRouter;
    this.phaseEmitter = phaseEmitter;

    this.ws = null;
    this.connected = false;
    this.reconnectAttempt = 0;
    this.reconnectTimer = null;
    this.lastDisconnectAt = 0;
  }

  start() {
    // SW 기동 즉시 connect + register
    this.connectAndRegister({ reason: 'sw_start' });
  }

  connectAndRegister({ reason = 'unknown' } = {}) {
    this.clearReconnectTimer();

    try {
      this.ws = new WebSocket(this.wsUrl);
    } catch (error) {
      this.scheduleReconnect({ reason: 'constructor_failed', error });
      return;
    }

    this.ws.onopen = async () => {
      this.connected = true;
      this.reconnectAttempt = 0;

      const token = await this.tokenProvider();
      this.send({
        type: 'register',
        role: 'extension',
        token,
        reason,
      });

      // SW 재시작/재연결 직후 초기 상태 phase emit
      this.phaseEmitter.emit({
        phase: 'EXTENSION_RECONNECTED',
        detail: { reason, ts: nowMs() },
      });

      this.requestRouter.onSocketReady();
    };

    this.ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      this.requestRouter.routeIncoming(msg);
    };

    this.ws.onclose = (event) => {
      this.connected = false;
      this.lastDisconnectAt = nowMs();
      this.requestRouter.onSocketClosed({ code: event.code, reason: event.reason });
      this.scheduleReconnect({ reason: 'socket_closed' });
    };

    this.ws.onerror = () => {
      // onclose에서 재연결을 일원화
    };
  }

  scheduleReconnect({ reason, error } = {}) {
    if (this.reconnectTimer) {
      return;
    }

    const backoff = Math.min(
      WS_BACKOFF_STEPS_MS[Math.min(this.reconnectAttempt, WS_BACKOFF_STEPS_MS.length - 1)],
      WS_BACKOFF_CAP_MS,
    );

    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.reconnectAttempt += 1;
      this.connectAndRegister({ reason: `reconnect:${reason}` });
    }, backoff);

    if (error) {
      console.warn('[WsManager] reconnect scheduled', { reason, backoff, error: String(error) });
    }
  }

  clearReconnectTimer() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  send(payload) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      return false;
    }

    this.ws.send(JSON.stringify(payload));
    return true;
  }
}

class PhaseEmitter {
  constructor({ wsManager }) {
    this.wsManager = wsManager;
  }

  emit({ phase, detail = null, requestId = null }) {
    this.wsManager.send({
      type: 'ai_phase',
      request_id: requestId,
      data: {
        phase,
        detail,
        elapsed_ms: 0,
      },
    });
  }
}

class RequestRouter {
  constructor() {
    this.handlers = new Map();
  }

  onSocketReady() {
    // 백엔드 grace window 중 대기 중인 요청은 재전송 트리거로 복구됨
  }

  onSocketClosed() {}

  routeIncoming(msg) {
    const handler = this.handlers.get(msg.request_id);
    if (handler) {
      handler(msg);
    }
  }
}

const requestRouter = new RequestRouter();
const wsManager = new WsManager({
  wsUrl: 'ws://localhost:8000/ws',
  tokenProvider: async () => 'dev-token',
  requestRouter,
  phaseEmitter: { emit: () => {} },
});

const phaseEmitter = new PhaseEmitter({ wsManager });
wsManager.phaseEmitter = phaseEmitter;
wsManager.start();

export { WsManager, RequestRouter, PhaseEmitter };
