import Constants from "expo-constants";

const fallback =
  (Constants.expoConfig?.extra as { apiUrl?: string } | undefined)?.apiUrl ??
  "http://127.0.0.1:8000";

export const API_URL = (
  process.env.EXPO_PUBLIC_API_URL?.trim() || fallback
).replace(/\/$/, "");
