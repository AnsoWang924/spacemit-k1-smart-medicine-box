export function createPillCountApi(call) {
  return {
    preload: (input, requestOptions = {}) => call("v2", "/pill-count/preload", { timeoutMs: 30_000, ...requestOptions, method: "POST", body: input }),
    recognize: (input, requestOptions = {}) => call("v2", "/pill-count/recognitions", { timeoutMs: 50_000, ...requestOptions, method: "POST", body: input }),
    release: (input, requestOptions = {}) => call("v2", "/pill-count/release", { keepalive: true, ...requestOptions, method: "POST", body: input }),
    status: (sessionId, requestOptions = {}) => call("v2", `/pill-count/status?sessionId=${encodeURIComponent(sessionId || "")}`, requestOptions),
  };
}
