import { API_URL } from "@/constants/config";
import type {
  AlertHistoryItem,
  StatusItem,
  WatchlistCreate,
  WatchlistItem,
} from "@/lib/types";

class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });

  if (!response.ok) {
    const text = await response.text();
    throw new ApiError(text || response.statusText, response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<{ status: string; watchlist_count: number }>("/api/health"),

  getStatus: (market = "ALL") =>
    request<StatusItem[]>(`/api/status?market=${encodeURIComponent(market)}`),

  getWatchlist: () => request<WatchlistItem[]>("/api/watchlist"),

  addWatchlist: (payload: WatchlistCreate) =>
    request<WatchlistItem>("/api/watchlist", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  removeWatchlist: (ticker: string) =>
    request<{ removed: string }>(`/api/watchlist/${encodeURIComponent(ticker)}`, {
      method: "DELETE",
    }),

  getAlerts: (limit = 100) =>
    request<AlertHistoryItem[]>(`/api/alerts?limit=${limit}`),

  triggerScan: (market = "ALL", dryRun = false) =>
    request("/api/scan", {
      method: "POST",
      body: JSON.stringify({ market, dry_run: dryRun }),
    }),

  registerPushToken: (expo_push_token: string, device_label = "") =>
    request<{ registered: boolean; token_count: number }>("/api/push-tokens", {
      method: "POST",
      body: JSON.stringify({ expo_push_token, device_label }),
    }),
};

export { ApiError };
