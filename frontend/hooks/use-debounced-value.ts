"use client";

import * as React from "react";

/**
 * A value that settles before anything acts on it.
 *
 * For search boxes whose term reaches the SERVER: without this, "akshat" is six
 * queries against a table that only grows, five of whose answers are thrown away
 * before they arrive. The delay is per keystroke, so a fast typist issues one.
 */
export function useDebouncedValue<T>(value: T, delayMs = 300): T {
  const [settled, setSettled] = React.useState(value);

  React.useEffect(() => {
    const t = setTimeout(() => setSettled(value), delayMs);
    return () => clearTimeout(t);
  }, [value, delayMs]);

  return settled;
}
