export function createSystemApi(call) {
  return {
    metrics: (requestOptions = {}) => call("v2", "/system/metrics", { timeoutMs: 1_500, ...requestOptions }),
  };
}
