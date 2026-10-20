export function createHealthApi(call) {
  return {
    live: () => call("v2", "/health/live"),
    ready: () => call("v2", "/health/ready"),
    capabilities: () => call("v2", "/capabilities"),
  };
}
