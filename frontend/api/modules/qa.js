export function createQaApi(call) {
  return {
    health: (requestOptions = {}) => call("v1", "/health", requestOptions),
    ask: (input, requestOptions = {}) => call("v1", "/medicine-qa/messages", { timeoutMs: 55_000, ...requestOptions, method: "POST", body: input }),
  };
}
