import { PanelExtensionContext } from "@foxglove/extension";
import { ReactElement, useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";

import "./styles.css";

type CommandResponse = {
  accepted?: boolean;
  applied?: boolean;
  message?: string;
};

type Control = {
  id: string;
  label: string;
  service: string;
  title: string;
  tone?: "danger" | "primary";
};

type ControlGroup = {
  label: string;
  controls: Control[];
};

const CONTROL_GROUPS: ControlGroup[] = [
  {
    label: "Robot",
    controls: [
      {
        id: "hold",
        label: "Hold",
        service: "/teleop/hold",
        title: "Stop Cartesian motion and release the clutch home",
        tone: "danger",
      },
      {
        id: "resume",
        label: "Resume",
        service: "/teleop/resume",
        title: "Resume and re-clutch from the current controller and EEF poses",
        tone: "primary",
      },
    ],
  },
  {
    label: "Episode",
    controls: [
      {
        id: "previous",
        label: "← Previous",
        service: "/teleop/episode/previous",
        title: "Load the previous episode seed",
      },
      {
        id: "reset",
        label: "Reset",
        service: "/teleop/episode/reset",
        title: "Reset the current episode seed",
      },
      {
        id: "next",
        label: "Next →",
        service: "/teleop/episode/next",
        title: "Load the next episode seed",
      },
    ],
  },
  {
    label: "Recording",
    controls: [
      {
        id: "record",
        label: "● Start",
        service: "/teleop/recording/start",
        title: "Start a synchronized action and camera recording",
        tone: "primary",
      },
      {
        id: "save",
        label: "■ Save",
        service: "/teleop/recording/stop",
        title: "Finalize and save the current recording",
      },
      {
        id: "discard",
        label: "Discard",
        service: "/teleop/recording/discard",
        title: "Permanently delete the current recording",
        tone: "danger",
      },
    ],
  },
];

function responseMessage(response: unknown, fallback: string): string {
  if (typeof response !== "object" || response == undefined) {
    return fallback;
  }
  const result = response as CommandResponse;
  if (result.accepted !== true || result.applied !== true) {
    throw new Error(result.message ?? "Command rejected by backend");
  }
  return result.message ?? fallback;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function TeleopControls({ context }: { context: PanelExtensionContext }): ReactElement {
  const [pending, setPending] = useState<string>();
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string }>();
  const noticeTimer = useRef<number>();
  const requestInFlight = useRef(false);

  const showNotice = useCallback((tone: "ok" | "error", text: string) => {
    window.clearTimeout(noticeTimer.current);
    setNotice({ tone, text });
    noticeTimer.current = window.setTimeout(() => {
      setNotice(undefined);
    }, 3000);
  }, []);

  useEffect(
    () => () => {
      window.clearTimeout(noticeTimer.current);
    },
    [],
  );

  const run = useCallback(
    async (control: Control) => {
      if (requestInFlight.current) {
        return;
      }
      if (context.callService == undefined) {
        showNotice("error", "Services unavailable");
        return;
      }

      if (
        control.id === "discard" &&
        !window.confirm("Discard and permanently delete the current recording?")
      ) {
        return;
      }

      requestInFlight.current = true;
      setPending(control.id);
      setNotice(undefined);
      try {
        const response = await context.callService(control.service, {});
        showNotice("ok", responseMessage(response, `${control.label} applied`));
      } catch (error) {
        showNotice("error", errorMessage(error));
      } finally {
        requestInFlight.current = false;
        setPending(undefined);
      }
    },
    [context, showNotice],
  );

  return (
    <div className="teleop-panel" aria-busy={pending != undefined}>
      <div className="teleop-groups">
        {CONTROL_GROUPS.map((group) => (
          <section className="teleop-group" key={group.label}>
            <div className="teleop-group-label">{group.label}</div>
            <div className="teleop-buttons">
              {group.controls.map((control) => (
                <button
                  className={`teleop-button${control.tone ? ` teleop-button--${control.tone}` : ""}`}
                  disabled={pending != undefined}
                  key={control.id}
                  onClick={() => {
                    void run(control);
                  }}
                  title={control.title}
                  type="button"
                >
                  {pending === control.id ? "Working…" : control.label}
                </button>
              ))}
            </div>
          </section>
        ))}
      </div>
      <div
        className={`teleop-notice${notice ? ` teleop-notice--${notice.tone}` : ""}`}
        role="status"
      >
        {notice?.text ?? ""}
      </div>
    </div>
  );
}

export function initTeleopControls(context: PanelExtensionContext): () => void {
  context.setDefaultPanelTitle("Controls");
  const root = createRoot(context.panelElement);
  root.render(<TeleopControls context={context} />);
  return () => {
    root.unmount();
  };
}
