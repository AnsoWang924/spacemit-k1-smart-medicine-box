export function createPrescriptionOcrApi(call) {
  return {
    preload: (input, requestOptions = {}) => call("v2", "/prescription-ocr/preload", { timeoutMs: 120_000, ...requestOptions, method: "POST", body: input }),
    recognize: (input, requestOptions = {}) => call("v2", "/prescription-ocr/recognitions", { timeoutMs: 120_000, ...requestOptions, method: "POST", body: input }),
    release: (input, requestOptions = {}) => call("v2", "/prescription-ocr/release", { keepalive: true, ...requestOptions, method: "POST", body: input }),
    status: (sessionId, requestOptions = {}) => call("v2", `/prescription-ocr/status?sessionId=${encodeURIComponent(sessionId || "")}`, requestOptions),
  };
}
