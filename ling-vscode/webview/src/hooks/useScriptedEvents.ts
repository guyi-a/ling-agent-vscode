import { useEffect, type Dispatch } from "react";
import { MOCK_EVENTS } from "../mockData";
import type { UiAction } from "../protocol";

export function useScriptedEvents(
  dispatch: Dispatch<UiAction>,
  streamKey: number,
  enabled: boolean,
): void {
  useEffect(() => {
    if (!enabled) {
      return;
    }

    dispatch({ type: "session.reset" });

    const timers = MOCK_EVENTS.map(({ delayMs, event }) =>
      window.setTimeout(() => dispatch(event), delayMs),
    );

    return () => {
      timers.forEach((timer) => window.clearTimeout(timer));
    };
  }, [dispatch, enabled, streamKey]);
}
