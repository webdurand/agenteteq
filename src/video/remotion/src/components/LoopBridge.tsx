import { useCurrentFrame, useVideoConfig, interpolate } from "remotion";

interface LoopBridgeProps {
  /** Number of frames at the end to fade out (creates seamless loop feel) */
  fadeFrames?: number;
  children: React.ReactNode;
}

/**
 * Wraps the entire video to create a loop-friendly ending.
 * Fades the last few frames to create a seamless visual loop.
 */
export const LoopBridge: React.FC<LoopBridgeProps> = ({
  fadeFrames = 8,
  children,
}) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();

  const fadeStart = durationInFrames - fadeFrames;

  const opacity =
    frame >= fadeStart
      ? interpolate(frame, [fadeStart, durationInFrames], [1, 0.85], {
          extrapolateRight: "clamp",
        })
      : 1;

  return (
    <div style={{ width: "100%", height: "100%", opacity }}>{children}</div>
  );
};
