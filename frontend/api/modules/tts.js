export function createTtsApi(call) {
  return {
    synthesize: (input) => call("v1", "/tts/synthesize", { method: "POST", body: input, timeoutMs: 90_000 }),
    warmup: (input = {}) => call("v1", "/tts/warmup", { method: "POST", body: input, timeoutMs: 120_000 }),
  };
}
