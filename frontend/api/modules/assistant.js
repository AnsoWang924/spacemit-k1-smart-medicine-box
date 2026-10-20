export function createAssistantApi(call) {
  return {
    preload: (input, requestOptions = {}) => call("v2", "/assistant/preload", { timeoutMs: 90_000, ...requestOptions, method: "POST", body: input }),
    release: (input, requestOptions = {}) => call("v2", "/assistant/release", { keepalive: true, ...requestOptions, method: "POST", body: input }),
    status: (requestOptions = {}) => call("v2", "/assistant/status", requestOptions),
  };
}
