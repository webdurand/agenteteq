import { useCurrentFrame, useVideoConfig, spring } from "remotion";
import { createTikTokStyleCaptions, type Caption } from "@remotion/captions";

// Safe zone: captions between Y=1200-1400px (above Instagram bottom UI)
const CAPTION_Y = 1250;

interface TikTokCaptionsProps {
  captions: Caption[];
}

export const TikTokCaptions: React.FC<TikTokCaptionsProps> = ({ captions }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const timeMs = (frame / fps) * 1000;

  if (!captions || captions.length === 0) return null;

  const { pages } = createTikTokStyleCaptions({
    captions,
    combineTokensWithinMilliseconds: 500,
  });

  const currentPage = pages.find(
    (page) =>
      timeMs >= page.startMs && timeMs < page.startMs + page.durationMs
  );

  if (!currentPage) return null;

  return (
    <div
      style={{
        position: "absolute",
        top: CAPTION_Y,
        left: 0,
        right: 0,
        display: "flex",
        justifyContent: "center",
        zIndex: 100,
      }}
    >
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          justifyContent: "center",
          maxWidth: "85%",
          gap: "4px 6px",
        }}
      >
        {currentPage.tokens.map((token, i) => {
          const isActive = timeMs >= token.fromMs && timeMs < token.toMs;
          const wordStartFrame = (token.fromMs / 1000) * fps;

          const bounce = spring({
            frame: frame - wordStartFrame,
            fps,
            config: { damping: 10, mass: 0.5 },
          });

          const scale = isActive ? 0.95 + bounce * 0.05 : 1;

          return (
            <span
              key={`${i}-${token.text}`}
              style={{
                display: "inline-block",
                fontFamily: "Inter, Helvetica Neue, Arial, sans-serif",
                fontSize: 48,
                fontWeight: 800,
                lineHeight: 1.3,
                transform: `scale(${scale})`,
                color: isActive ? "#FFD700" : "#FFFFFF",
                backgroundColor: isActive
                  ? "rgba(0, 0, 0, 0.85)"
                  : "rgba(0, 0, 0, 0.6)",
                padding: "4px 10px",
                borderRadius: 6,
                textShadow: isActive
                  ? "0 0 8px rgba(255, 215, 0, 0.5)"
                  : "1px 2px 4px rgba(0, 0, 0, 0.8)",
                transition: "color 0.1s, background-color 0.1s",
              }}
            >
              {token.text}
            </span>
          );
        })}
      </div>
    </div>
  );
};
