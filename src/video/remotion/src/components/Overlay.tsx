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
  animation?: "slide_up" | "scale_pop" | "fade_blur" | "slide_left";
}

export const Overlay: React.FC<OverlayProps> = ({
  text,
  startFrame,
  durationInFrames,
  position = "top",
  imageUrl,
  animation = "slide_up",
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const localFrame = frame - startFrame;
  if (localFrame < 0 || localFrame >= durationInFrames) return null;

  // Entrance animation (spring)
  const entrance = spring({
    frame: localFrame,
    fps,
    config: { damping: 14, mass: 0.5 },
    durationInFrames: 12,
  });

  // Exit fade (last 8 frames)
  const exitStart = durationInFrames - 8;
  const opacity =
    localFrame >= exitStart
      ? interpolate(localFrame, [exitStart, durationInFrames], [1, 0], {
          extrapolateRight: "clamp",
        })
      : 1;

  const yOffset = position === "top" ? SAFE_TOP + 50 : position === "bottom" ? SAFE_BOTTOM - 200 : 900;

  // Animation variants
  let transform = "";
  let filter = "";
  switch (animation) {
    case "scale_pop":
      const popScale = interpolate(entrance, [0, 1], [0.6, 1]);
      transform = `scale(${popScale})`;
      break;
    case "fade_blur":
      const blur = interpolate(entrance, [0, 1], [8, 0]);
      filter = `blur(${blur}px)`;
      transform = `translateY(${(1 - entrance) * 10}px)`;
      break;
    case "slide_left":
      const slideX = (1 - entrance) * 60;
      transform = `translateX(${slideX}px)`;
      break;
    case "slide_up":
    default:
      transform = `translateY(${(1 - entrance) * 30}px)`;
      break;
  }

  return (
    <div
      style={{
        position: "absolute",
        top: yOffset,
        left: SAFE_LEFT,
        right: 1080 - SAFE_RIGHT,
        opacity: opacity * entrance,
        transform,
        filter: filter || undefined,
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
            color: "#FFFFFF",
            fontFamily: "Inter, Helvetica Neue, Arial, sans-serif",
            fontSize: 38,
            fontWeight: 800,
            padding: "8px 16px",
            borderRadius: 8,
            textAlign: "center",
            maxWidth: "100%",
            textTransform: "uppercase" as const,
            letterSpacing: 1.5,
            textShadow:
              "2px 2px 8px rgba(0,0,0,0.9), -1px -1px 4px rgba(0,0,0,0.7), 0 0 20px rgba(0,0,0,0.5)",
          }}
        >
          {text}
        </div>
      )}
    </div>
  );
};
