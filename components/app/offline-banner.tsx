"use client";

import * as React from "react";
import { WifiOff } from "lucide-react";

export function OfflineBanner() {
  const [offline, setOffline] = React.useState(false);

  React.useEffect(() => {
    const update = () => setOffline(!navigator.onLine);
    update();
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    return () => {
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
    };
  }, []);

  if (!offline) return null;

  return (
    // Elevated warning banner — brand-aware surface with mono text + hairline border
    <div
      role="alert"
      aria-live="polite"
      className="border-b border-line-soft bg-warning/10 text-warning-foreground flex items-center justify-center gap-2 px-3 py-2 font-mono text-xs font-semibold"
    >
      <WifiOff className="size-3.5 shrink-0" aria-hidden />
      <span>You&apos;re offline. Changes will sync when the connection returns.</span>
    </div>
  );
}
