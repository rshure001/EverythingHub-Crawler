export function createTimedFetch(baseFetch = fetch, timeoutMs = 6500, cooldownMs = 15_000) {
  if (typeof baseFetch !== "function") {
    throw new TypeError("baseFetch must be a function");
  }
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1) {
    throw new RangeError("timeoutMs must be a positive safe integer");
  }
  if (!Number.isSafeInteger(cooldownMs) || cooldownMs < 0) {
    throw new RangeError("cooldownMs must be a non-negative safe integer");
  }

  // Each Edge Function isolate keeps a short-lived circuit so a recovering
  // database is not probed on every search request. A later request performs
  // the half-open probe after the cooldown expires.
  let unavailableUntil = 0;

  return async (input, init = {}) => {
    if (Date.now() < unavailableUntil) {
      throw new Error("Database circuit open after recent transient failure");
    }

    const timeoutSignal = AbortSignal.timeout(timeoutMs);
    const signal = init.signal
      ? AbortSignal.any([init.signal, timeoutSignal])
      : timeoutSignal;

    try {
      const response = await baseFetch(input, { ...init, signal });
      if ([502, 503, 504].includes(response.status)) {
        unavailableUntil = Date.now() + cooldownMs;
      } else if (response.ok) {
        unavailableUntil = 0;
      }
      return response;
    } catch (error) {
      if (isTransientDatabaseError(error)) {
        unavailableUntil = Date.now() + cooldownMs;
      }
      throw error;
    }
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
  "PGRST002",
  "PGRST003",
]);

export function isTransientDatabaseError(error) {
  const code = String(error?.code ?? "").toUpperCase();
  const message = String(error?.message ?? error ?? "").toLowerCase();
  return TRANSIENT_DATABASE_CODES.has(code) ||
    /timed?\s*out|timeout|abort|fetch failed|connection (?:refused|reset|error)|database connection|not accepting connections|hot standby|circuit open/.test(message);
}
