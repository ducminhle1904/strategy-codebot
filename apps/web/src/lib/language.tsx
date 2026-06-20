"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import {
  LANGUAGE_STORAGE_KEY,
  getUiCopy,
  normalizeLanguage,
  type LanguagePreference,
  type UiCopy,
} from "@/lib/i18n";

type LanguageContextValue = {
  language: LanguagePreference;
  setLanguage: (language: LanguagePreference) => void;
  t: UiCopy;
};

const LanguageContext = createContext<LanguageContextValue | null>(null);
const LANGUAGE_CHANGE_EVENT = "strategy-codebot-language-change";

function readStoredLanguage(): LanguagePreference {
  if (typeof window === "undefined") {
    return "en";
  }
  try {
    return normalizeLanguage(window.localStorage.getItem(LANGUAGE_STORAGE_KEY));
  } catch {
    return "en";
  }
}

function serverLanguageSnapshot(): LanguagePreference {
  return "en";
}

function subscribeLanguage(callback: () => void) {
  if (typeof window === "undefined") {
    return () => undefined;
  }
  const handleStorage = (event: StorageEvent) => {
    if (event.key === LANGUAGE_STORAGE_KEY) {
      callback();
    }
  };
  window.addEventListener("storage", handleStorage);
  window.addEventListener(LANGUAGE_CHANGE_EVENT, callback);
  return () => {
    window.removeEventListener("storage", handleStorage);
    window.removeEventListener(LANGUAGE_CHANGE_EVENT, callback);
  };
}

export function LanguageProvider({ children }: { children: ReactNode }) {
  const language = useSyncExternalStore(
    subscribeLanguage,
    readStoredLanguage,
    serverLanguageSnapshot
  );

  const setLanguage = useCallback((nextLanguage: LanguagePreference) => {
    try {
      window.localStorage.setItem(LANGUAGE_STORAGE_KEY, nextLanguage);
      window.dispatchEvent(new Event(LANGUAGE_CHANGE_EVENT));
    } catch {
      // Local preference persistence is best effort.
    }
  }, []);

  const value = useMemo(
    () => ({
      language,
      setLanguage,
      t: getUiCopy(language),
    }),
    [language, setLanguage]
  );

  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>;
}

export function useI18n() {
  const context = useContext(LanguageContext);
  if (!context) {
    throw new Error("useI18n must be used within LanguageProvider");
  }
  return context;
}

export type { LanguagePreference };
