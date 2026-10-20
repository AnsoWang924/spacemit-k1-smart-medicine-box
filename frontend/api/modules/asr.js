export function createAsrApi(call) {
  return {
    preload: (input = {}, requestOptions = {}) => call("v1", "/asr/preload", { ...requestOptions, method: "POST", body: input }),
    start: (input = {}, requestOptions = {}) => call("v1", "/asr/recordings", { ...requestOptions, method: "POST", body: input }),
    stop: (jobId, input = {}, requestOptions = {}) => call("v1", `/asr/recordings/${encodeURIComponent(jobId)}/stop`, { ...requestOptions, method: "POST", body: input }),
    status: (jobId, requestOptions = {}) => call("v1", `/asr/recordings/${encodeURIComponent(jobId)}`, requestOptions),
  };
}
