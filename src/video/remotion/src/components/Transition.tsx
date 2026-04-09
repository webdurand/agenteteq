import { useCurrentFrame, interpolate } from "remotion";

interface TransitionProps {
  durationInFrames: number;
  type?: "crossfade" | "slide" | "zoom";
  children: React.ReactNode;
}

/**
 * Wraps content with an entrance transition.
 * Apply at the start of each scene for smooth transitions.
 */
export const Transition: React.FC<TransitionProps> = ({
  durationInFrames,
  type = "crossfade",
  children,
}) => {
  const frame = useCurrentFrame();

  // Transition applies to the first few frames of the scene
  const transitionFrames = Math.min(8, durationInFrames);

  if (frame >= transitionFrames) {
    return <>{children}</>;
  }

  const progress = interpolate(frame, [0, transitionFrames], [0, 1], {
    extrapolateRight: "clamp",
  });

  let style: React.CSSProperties = {};

  switch (type) {
    case "crossfade":
      style = { opacity: progress };
      break;
    case "slide":
      style = {
        opacity: progress,
        transform: `translateX(${(1 - progress) * 50}px)`,
      };
      break;
    case "zoom":
      style = {
        opacity: progress,
        transform: `scale(${0.9 + progress * 0.1})`,
      };
      break;
  }

  return (
    <div style={{ width: "100%", height: "100%", ...style }}>{children}</div>
  );
};
