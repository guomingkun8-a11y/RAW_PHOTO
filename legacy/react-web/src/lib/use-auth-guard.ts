"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { fetchCurrentUser } from "@/lib/api";
import {
  clearStoredAuthSession,
  getDefaultRouteForRole,
  getStoredAuthSession,
  setStoredAuthSession,
  type AuthRole,
  type StoredAuthSession,
} from "@/store/auth";

type UseAuthGuardResult = {
  isCheckingAuth: boolean;
  session: StoredAuthSession | null;
};

const AUTH_CHECK_TIMEOUT_MS = 8000;
const AUTH_STORAGE_TIMEOUT_MS = 3000;

function isAllowed(session: StoredAuthSession, allowedRoles?: AuthRole[]) {
  return !allowedRoles || allowedRoles.length === 0 || allowedRoles.includes(session.role);
}

function loginPath(pathname: string | null) {
  const next = pathname && pathname !== "/login" ? `?next=${encodeURIComponent(pathname)}` : "";
  return `/login${next}`;
}

function withTimeout<T>(promise: Promise<T>, timeoutMs: number, message: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error(message)), timeoutMs);
  });
  return Promise.race([promise, timeout]).finally(() => {
    if (timer) {
      clearTimeout(timer);
    }
  });
}

async function safeClearStoredAuthSession() {
  try {
    await withTimeout(clearStoredAuthSession(), AUTH_STORAGE_TIMEOUT_MS, "clear auth session timeout");
  } catch {
    // Ignore storage cleanup failures so auth guards can leave the loading state.
  }
}

export function useAuthGuard(allowedRoles?: AuthRole[]): UseAuthGuardResult {
  const router = useRouter();
  const pathname = usePathname();
  const allowedRoleKey = allowedRoles?.join(",") || "";
  const [isCheckingAuth, setIsCheckingAuth] = useState(true);
  const [session, setSession] = useState<StoredAuthSession | null>(null);

  useEffect(() => {
    let cancelled = false;

    const check = async () => {
      let stored: StoredAuthSession | null = null;
      try {
        stored = await withTimeout(
          getStoredAuthSession(),
          AUTH_STORAGE_TIMEOUT_MS,
          "read auth session timeout",
        );
      } catch {
        await safeClearStoredAuthSession();
      }

      if (!stored) {
        if (!cancelled) {
          setSession(null);
          setIsCheckingAuth(false);
          router.replace(loginPath(pathname));
        }
        return;
      }

      try {
        const current = await withTimeout(fetchCurrentUser(), AUTH_CHECK_TIMEOUT_MS, "auth check timeout");
        const nextSession: StoredAuthSession = {
          key: stored.key,
          role: current.role,
          subjectId: current.subject_id,
          username: current.username || stored.username,
          name: current.name,
        };
        await withTimeout(setStoredAuthSession(nextSession), AUTH_STORAGE_TIMEOUT_MS, "save auth session timeout");
        if (!isAllowed(nextSession, allowedRoles)) {
          router.replace(getDefaultRouteForRole(nextSession.role));
          return;
        }
        if (!cancelled) {
          setSession(nextSession);
          setIsCheckingAuth(false);
        }
      } catch {
        await safeClearStoredAuthSession();
        if (!cancelled) {
          setSession(null);
          setIsCheckingAuth(false);
          router.replace(loginPath(pathname));
        }
      }
    };

    void check().catch(async () => {
      await safeClearStoredAuthSession();
      if (!cancelled) {
        setSession(null);
        setIsCheckingAuth(false);
        router.replace(loginPath(pathname));
      }
    });
    return () => {
      cancelled = true;
    };
  }, [router, pathname, allowedRoleKey]);

  return { isCheckingAuth, session };
}

export function useRedirectIfAuthenticated() {
  const router = useRouter();
  const [isCheckingAuth, setIsCheckingAuth] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const check = async () => {
      let stored: StoredAuthSession | null = null;
      try {
        stored = await withTimeout(
          getStoredAuthSession(),
          AUTH_STORAGE_TIMEOUT_MS,
          "read auth session timeout",
        );
      } catch {
        await safeClearStoredAuthSession();
      }

      if (!stored) {
        if (!cancelled) setIsCheckingAuth(false);
        return;
      }
      try {
        const current = await withTimeout(fetchCurrentUser(), AUTH_CHECK_TIMEOUT_MS, "auth check timeout");
        const nextSession: StoredAuthSession = {
          key: stored.key,
          role: current.role,
          subjectId: current.subject_id,
          username: current.username || stored.username,
          name: current.name,
        };
        await withTimeout(setStoredAuthSession(nextSession), AUTH_STORAGE_TIMEOUT_MS, "save auth session timeout");
        const params = typeof window !== "undefined" ? new URLSearchParams(window.location.search) : null;
        const next = params?.get("next") || getDefaultRouteForRole(nextSession.role);
        router.replace(next.startsWith("/") ? next : getDefaultRouteForRole(nextSession.role));
      } catch {
        await safeClearStoredAuthSession();
        if (!cancelled) setIsCheckingAuth(false);
      }
    };
    void check().catch(async () => {
      await safeClearStoredAuthSession();
      if (!cancelled) setIsCheckingAuth(false);
    });
    return () => {
      cancelled = true;
    };
  }, [router]);

  return { isCheckingAuth };
}
