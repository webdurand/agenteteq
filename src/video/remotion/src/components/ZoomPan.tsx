import { useCurrentFrame, useVideoConfig, interpolate, spring } from "remotion";

export type MovementType =
  | "zoom_in_face"
  | "zoom_out"
  | "ken_burns"
  | "zoom_pulse"
  | "whip_pan"
  | "drift_right"
  | "drift_left"
  | "dolly_zoom"
  | "static";

interface ZoomPanProps {
  movement: MovementType;
  durationInFrames: number;
  startFrame: number;
  children: React.ReactNode;
}

export const ZoomPan: React.FC<ZoomPanProps> = ({
  movement,
  durationInFrames,
  startFrame,
  children,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const localFrame = frame - startFrame;
  if (localFrame < 0 || localFrame >= durationInFrames) {
    return <div style={{ width: "100%", height: "100%" }}>{children}</div>;
  }

  const progress = localFrame / durationInFrames;
  let scale = 1;
  let translateX = 0;
  let translateY = 0;

  switch (movement) {
    case "zoom_in_face": {
      // Quick zoom: 1.0 → 1.3 with spring
      const springVal = spring({
        frame: localFrame,
        fps,
        config: { damping: 15, mass: 0.8 },
        durationInFrames: Math.min(15, durationInFrames),
      });
      scale = 1 + springVal * 0.3;
      break;
    }
    case "zoom_out": {
      // Zoom out: 1.2 → 1.0
      scale = interpolate(localFrame, [0, durationInFrames], [1.2, 1], {
        extrapolateRight: "clamp",
      });
      break;
    }
    case "ken_burns": {
      // Slow zoom + pan: 1.0 → 1.15 with subtle horizontal drift
      scale = interpolate(localFrame, [0, durationInFrames], [1, 1.15], {
        extrapolateRight: "clamp",
      });
      translateX = interpolate(localFrame, [0, durationInFrames], [0, -20], {
        extrapolateRight: "clamp",
      });
      translateY = interpolate(localFrame, [0, durationInFrames], [0, -10], {
        extrapolateRight: "clamp",
      });
      break;
    }
    case "zoom_pulse": {
      // Pulse: 1.0 → 1.15 → 1.0 → 1.15 → 1.0 (rhythmic)
      scale = interpolate(
        localFrame,
        [
          0,
          durationInFrames * 0.25,
          durationInFrames * 0.5,
          durationInFrames * 0.75,
          durationInFrames,
        ],
        [1, 1.15, 1, 1.15, 1],
        { extrapolateRight: "clamp" }
      );
      break;
    }
    case "whip_pan": {
      // Fast horizontal entry with overshoot (6 frames) + slight scale
      const whipSpring = spring({
        frame: localFrame,
        fps,
        config: { damping: 12, stiffness: 200, mass: 0.6 },
        durationInFrames: Math.min(8, durationInFrames),
      });
      translateX = interpolate(whipSpring, [0, 1], [-540, 0]);
      scale = 1 + (1 - whipSpring) * 0.05;
      break;
    }
    case "drift_right": {
      // Slow constant horizontal drift to the right
      translateX = interpolate(localFrame, [0, durationInFrames], [0, 30], {
        extrapolateRight: "clamp",
      });
      scale = 1.05;
      break;
    }
    case "drift_left": {
      // Slow constant horizontal drift to the left
      translateX = interpolate(localFrame, [0, durationInFrames], [0, -30], {
        extrapolateRight: "clamp",
      });
      scale = 1.05;
      break;
    }
    case "dolly_zoom": {
      // Vertigo effect: zoom in while pulling back (scale up + translateY shift)
      scale = interpolate(localFrame, [0, durationInFrames], [1, 1.15], {
        extrapolateRight: "clamp",
      });
      translateY = interpolate(localFrame, [0, durationInFrames], [0, -20], {
        extrapolateRight: "clamp",
      });
      break;
    }
    case "static":
    default:
      break;
  }

  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          width: "100%",
          height: "100%",
          transform: `scale(${scale}) translate(${translateX}px, ${translateY}px)`,
          transformOrigin: "center center",
        }}
      >
        {children}
      </div>
    </div>
  );
};
