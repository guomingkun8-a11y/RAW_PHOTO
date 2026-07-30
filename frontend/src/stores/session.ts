import { reactive } from "vue";

import { fetchCurrentUser } from "@/lib/api";
import {
  clearStoredAuthSession,
  getStoredAuthSession,
  setStoredAuthSession,
  type StoredAuthSession,
} from "@/stores/auth";

export const sessionState = reactive<{
  session: StoredAuthSession | null;
  ready: boolean;
}>({
  session: null,
  ready: false,
});

let validationPromise: Promise<StoredAuthSession | null> | null = null;

export async function validateSession(force = false) {
  if (!force && sessionState.ready) return sessionState.session;
  if (!validationPromise) {
    validationPromise = (async () => {
      const stored = await getStoredAuthSession();
      if (!stored) {
        sessionState.session = null;
        sessionState.ready = true;
        return null;
      }
      try {
        const current = await fetchCurrentUser();
        const session: StoredAuthSession = {
          key: stored.key,
          role: current.role,
          subjectId: current.subject_id,
          username: current.username || stored.username,
          name: current.name,
        };
        await setStoredAuthSession(session);
        sessionState.session = session;
        sessionState.ready = true;
        return session;
      } catch {
        await clearStoredAuthSession();
        sessionState.session = null;
        sessionState.ready = true;
        return null;
      }
    })().finally(() => {
      validationPromise = null;
    });
  }
  return validationPromise;
}

export function setSession(session: StoredAuthSession | null) {
  sessionState.session = session;
  sessionState.ready = true;
}
