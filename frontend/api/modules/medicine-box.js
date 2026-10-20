export function createMedicineBoxApi(call) {
  return {
    preload: (input, requestOptions = {}) => call("v2", "/medicine-box/preload", { timeoutMs: 30_000, ...requestOptions, method: "POST", body: input }),
    recognize: (input, requestOptions = {}) => call("v2", "/medicine-box/recognitions", { timeoutMs: 55_000, ...requestOptions, method: "POST", body: input }),
    release: (input, requestOptions = {}) => call("v2", "/medicine-box/release", { keepalive: true, ...requestOptions, method: "POST", body: input }),
    status: (sessionId, requestOptions = {}) => call("v2", `/medicine-box/status?sessionId=${encodeURIComponent(sessionId || "")}`, requestOptions),
  };
}
