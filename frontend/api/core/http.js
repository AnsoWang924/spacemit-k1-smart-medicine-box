const DEFAULT_TIMEOUT_MS = 45_000;

export class GatewayError extends Error {
  constructor({ code = "gateway_request_failed", message, status = 0, requestId = "", retryable = false, data = null, details = {} }) {
    super(message || code);
    this.name = "GatewayError";
    this.code = code;
    this.status = status;
    this.requestId = requestId;
    this.retryable = retryable;
    this.data = data;
    this.stage = details.stage || "";
    this.taskId = details.taskId || "";
    this.sessionId = details.sessionId || "";
    this.causeCode = details.causeCode || "";
    this.released = details.released;
    this.activeTask = details.activeTask || null;
    this.details = details;
  }
}

export function createRequestId() {
  if (globalThis.crypto?.randomUUID) return `web_${globalThis.crypto.randomUUID()}`;
  return `web_${Date.now()}_${Math.random().toString(16).slice(2)}`;
}

export function resolveGatewayOrigin(documentRef = globalThis.document) {
  const configured = documentRef?.documentElement?.dataset?.apiBase || "";
  if (configured) return configured.replace(/\/api\/v1\/?$/, "").replace(/\/$/, "");
  if (documentRef?.location?.protocol === "file:") return "http://127.0.0.1:8799";
  const location = documentRef?.location;
  if (location?.port === "8799" && location.origin) return location.origin;
  const protocol = location?.protocol || "http:";
  const hostname = location?.hostname || "127.0.0.1";
  return `${protocol}//${hostname}:8799`;
}

export async function requestGateway({
  origin = resolveGatewayOrigin(),
  version,
  path,
  method = "GET",
  body,
  timeoutMs = DEFAULT_TIMEOUT_MS,
  headers = {},
  keepalive = false,
}) {
  const controller = new AbortController();
  const requestId = headers["X-Request-Id"] || createRequestId();
  const timer = globalThis.setTimeout(() => controller.abort(), timeoutMs);
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;

  try {
    const response = await fetch(`${origin}/api/${version}${normalizedPath}`, {
      method,
      cache: "no-store",
      headers: { "Content-Type": "application/json", "X-Request-Id": requestId, ...headers },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
      keepalive,
    });
    const payload = await response.json().catch(() => ({}));
    const error = payload?.error;
    if (!response.ok || error) {
      throw new GatewayError({
        code: error?.code || `http_${response.status}`,
        message: error?.message || `HTTP ${response.status}`,
        status: response.status,
        requestId: payload?.requestId || requestId,
        retryable: Boolean(error?.retryable),
        data: payload?.data ?? null,
        details: error || {},
      });
    }
    return payload?.data ?? payload;
  } catch (error) {
    if (error.name === "AbortError") {
      throw new GatewayError({ code: "gateway_timeout", message: "Gateway request timed out", requestId, retryable: true, details: { stage: "gateway" } });
    }
    throw error;
  } finally {
    globalThis.clearTimeout(timer);
  }
}
