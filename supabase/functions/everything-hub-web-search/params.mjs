export function boundedInteger(raw, fallback, min, max) {
  if (raw === null || raw === undefined) return fallback;
  const value = String(raw).trim();
  if (!value || !/^-?\\d+$/.test(value)) return fallback;
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed)) return fallback;
  return Math.min(max, Math.max(min, parsed));
}

export function normalizeQuery(raw, maxLength = 160) {
  return String(raw ?? "").trim().slice(0, maxLength);
}

export function normalizeLane(raw, allowed) {
  const lane = String(raw ?? "").trim().toLowerCase();
  return allowed.includes(lane) ? lane : null;
}
