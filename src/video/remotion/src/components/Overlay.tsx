import { useCurrentFrame, useVideoConfig, spring, interpolate } from "remotion";

// Safe zone boundaries
const SAFE_LEFT = 120;
const SAFE_RIGHT = 960;
const SAFE_TOP = 200;
const SAFE_BOTTOM = 1600;

interface OverlayProps {
  text: string;
  startFrame: number;
  durationInFrames: number;
  position?: "top" | "center" | "bottom";
  imageUrl?: string;
}

export const Overlay: React.FC<OverlayProps> = ({
  text,
  startFrame,
  durationInFrames,
  position = "top",
  imageUrl,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const localFrame = frame - startFrame;
  if (localFrame < 0 || localFrame >= durationInFrames) return null;

  // Entrance animation (spring)
  const entrance = spring({
    frame: localFrame,
    fps,
    config: { damping: 12, mass: 0.6 },
    durationInFrames: 15,
  });

  // Exit fade (last 10 frames)
  const exitStart = durationInFrames - 10;
  const opacity =
    localFrame >= exitStart
      ? interpolate(localFrame, [exitStart, durationInFrames], [1, 0], {
          extrapolateRight: "clamp",
        })
      : 1;

  const yOffset = position === "top" ? SAFE_TOP + 50 : position === "bottom" ? SAFE_BOTTOM - 200 : 900;

  const translateY = (1 - entrance) * 30;

  return (
    <div
      style={{
        position: "absolute",
        top: yOffset,
        left: SAFE_LEFT,
        right: 1080 - SAFE_RIGHT,
        opacity: opacity * entrance,
        transform: `translateY(${translateY}px)`,
        zIndex: 50,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 12,
      }}
    >
      {imageUrl && (
        <img
          src={imageUrl}
          style={{
            width: "80%",
            maxHeight: 400,
            objectFit: "cover",
            borderRadius: 12,
            boxShadow: "0 4px 20px rgba(0,0,0,0.4)",
          }}
        />
      )}
      {text && (
        <div
          style={{
            backgroundColor: "rgba(0, 0, 0, 0.75)",
            color: "#FFFFFF",
            fontFamily: "Inter, Helvetica Neue, Arial, sans-serif",
            fontSize: 36,
            fontWeight: 700,
            padding: "12px 20px",
            borderRadius: 8,
            textAlign: "center",
            maxWidth: "100%",
            textShadow: "1px 1px 3px rgba(0,0,0,0.5)",
          }}
        >
          {text}
        </div>
      )}
    </div>
  );
};
