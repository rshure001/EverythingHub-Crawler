export function createTimedFetch(baseFetch = fetch, timeoutMs = 6500) {
  if (typeof baseFetch !== "function") {
    throw new TypeError("baseFetch must be a function");
  }
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1) {
    throw new RangeError("timeoutMs must be a positive safe integer");
  }

  return (input, init = {}) => {
    const timeoutSignal = AbortSignal.timeout(timeoutMs);
    const signal = init.signal
      ? AbortSignal.any([init.signal, timeoutSignal])
      : timeoutSignal;
    return baseFetch(input, { ...init, signal });
  };
}

const TRANSIENT_DATABASE_CODES = new Set([
  "08000",
  "08001",
  "08003",
  "08006",
  "08007",
  "53300",
  "57P03",
  "PGRST000",
  "PGRST003",
]);

export function isTransientDatabaseError(error) {
  const code = String(error?.code ?? "").toUpperCase();
  const message = String(error?.message ?? error ?? "").toLowerCase();
  return TRANSIENT_DATABASE_CODES.has(code) ||
    /timed?\s*out|timeout|abort|fetch failed|connection (?:refused|reset|error)|database connection|not accepting connections|hot standby/.test(message);
}
