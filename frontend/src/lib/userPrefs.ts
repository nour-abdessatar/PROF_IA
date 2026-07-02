import type { Lang } from "@/lib/i18n";

export type UserPrefKey =
  | "onboarded"
  | "username"
  | "ai-name"
  | "lang"
  | "assessment-done"
  | "test-skipped"
  | "level";

export const userPrefKey = (userId: string, key: UserPrefKey) => `profia:${userId}:${key}`;

export function getUserPref(userId: string | undefined, key: UserPrefKey, fallback = "") {
  if (!userId || typeof window === "undefined") return fallback;
  return localStorage.getItem(userPrefKey(userId, key)) ?? fallback;
}

export function setUserPref(userId: string | undefined, key: UserPrefKey, value: string) {
  if (!userId) return;
  localStorage.setItem(userPrefKey(userId, key), value);
}

export function removeUserPref(userId: string | undefined, key: UserPrefKey) {
  if (!userId) return;
  localStorage.removeItem(userPrefKey(userId, key));
}

export function hasCompletedOnboarding(userId: string | undefined) {
  return getUserPref(userId, "onboarded") === "true";
}

export function getUserLang(userId: string | undefined): Lang | null {
  const lang = getUserPref(userId, "lang");
  return lang === "ar" || lang === "fr" ? lang : null;
}
