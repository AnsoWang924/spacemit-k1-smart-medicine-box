export function createCabinetApi(call) {
  return {
    open: ({ cabinetId, ...input }, requestOptions = {}) => call("v1", `/cabinets/${encodeURIComponent(cabinetId)}/open`, { ...requestOptions, method: "POST", body: input }),
    state: (cabinetId, requestOptions = {}) => call("v1", `/cabinets/${encodeURIComponent(cabinetId)}/state`, requestOptions),
  };
}
