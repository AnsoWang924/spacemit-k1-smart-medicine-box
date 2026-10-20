export function createFaceApi(call) {
  return {
    preload: (input, requestOptions = {}) => call("v2", "/face/preload", { ...requestOptions, method: "POST", body: input }),
    register: (input, requestOptions = {}) => call("v2", "/face/register", { timeoutMs: 70_000, ...requestOptions, method: "POST", body: input }),
    verify: (input, requestOptions = {}) => call("v2", "/face/verify", { timeoutMs: 55_000, ...requestOptions, method: "POST", body: input }),
    remove: (input, requestOptions = {}) => call("v2", "/face/remove", { ...requestOptions, method: "POST", body: input }),
    reset: (input, requestOptions = {}) => call("v2", "/face/reset", { ...requestOptions, method: "POST", body: input }),
    release: (input, requestOptions = {}) => call("v2", "/face/release", { keepalive: true, ...requestOptions, method: "POST", body: input }),
    status: (sessionId, requestOptions = {}) => call("v2", `/face/status?sessionId=${encodeURIComponent(sessionId || "")}`, requestOptions),
  };
}
