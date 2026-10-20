export function createDiagnosticsApi(call) {
  return {
    current: (requestOptions = {}) => call("v2", "/diagnose/current", requestOptions),
    task: (task, requestOptions = {}) => call("v2", `/diagnose/tasks/${encodeURIComponent(task)}`, requestOptions),
    deep: (task, requestOptions = {}) => call("v2", `/diagnose/deep/${encodeURIComponent(task)}`, requestOptions),
  };
}
