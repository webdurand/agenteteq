import { useCurrentFrame, useVideoConfig, spring } from "remotion";
import type { Caption } from "@remotion/captions";

// Safe zone: captions between Y=1200-1400px (above Instagram bottom UI)
const CAPTION_Y = 1250;

// Grouping thresholds (industry standard)
const PAUSE_THRESHOLD_MS = 500;  // Speech pause > 500ms = new group
const MAX_WORDS = 5;             // Never exceed 5 words per group
const MAX_CHARS = 35;            // Never exceed 35 chars per group
const SENTENCE_PUNCTUATION = /[.!?]$/;
const CLAUSE_PUNCTUATION = /[,;:]$/;

interface Word {
  text: string;
  startMs: number;
  endMs: number;
}

interface WordGroup {
  words: Word[];
  startMs: number;
  endMs: number;
}

/**
 * Smart caption grouping using speech pause detection + punctuation + limits.
 * Priority: 1) pause gaps  2) sentence punctuation  3) clause punctuation  4) word/char cap
 */
function buildWordGroups(captions: Caption[]): WordGroup[] {
  const groups: WordGroup[] = [];
  let current: Word[] = [];

  for (let i = 0; i < captions.length; i++) {
    const cap = captions[i];
    const word: Word = { text: cap.text, startMs: cap.startMs, endMs: cap.endMs };
    const prevWord = current.length > 0 ? current[current.length - 1] : null;

    // Decide if we should break BEFORE adding this word
    let shouldBreak = false;

    if (prevWord && current.length > 0) {
      const gap = cap.startMs - prevWord.endMs;
      const charCount = current.reduce((s, w) => s + w.text.length, 0);
      const prevText = prevWord.text.trim();

      // Priority 1: Speech pause
      if (gap > PAUSE_THRESHOLD_MS) shouldBreak = true;
      // Priority 2: Sentence punctuation on previous word
      else if (SENTENCE_PUNCTUATION.test(prevText)) shouldBreak = true;
      // Priority 3: Clause punctuation on previous word (only if group has 3+ words)
      else if (CLAUSE_PUNCTUATION.test(prevText) && current.length >= 3) shouldBreak = true;
      // Priority 4: Word count cap
      else if (current.length >= MAX_WORDS) shouldBreak = true;
      // Priority 5: Character count cap
      else if (charCount + word.text.length > MAX_CHARS) shouldBreak = true;
    }

    if (shouldBreak && current.length > 0) {
      groups.push({
        words: current,
        startMs: current[0].startMs,
        endMs: current[current.length - 1].endMs,
      });
      current = [];
    }

    current.push(word);
  }

  if (current.length > 0) {
    groups.push({
      words: current,
      startMs: current[0].startMs,
      endMs: current[current.length - 1].endMs,
    });
  }

  return groups;
}

interface TikTokCaptionsProps {
  captions: Caption[];
}

export const TikTokCaptions: React.FC<TikTokCaptionsProps> = ({ captions }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const timeMs = (frame / fps) * 1000;

  if (!captions || captions.length === 0) return null;

  const groups = buildWordGroups(captions);

  // Find the group that should be visible right now
  const currentGroup = groups.find(
    (g) => timeMs >= g.startMs && timeMs < g.endMs + 200 // 200ms grace period
  );

  if (!currentGroup) return null;

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
        {currentGroup.words.map((word, i) => {
          const isActive = timeMs >= word.startMs && timeMs < word.endMs;
          const isPast = timeMs >= word.endMs;
          const wordStartFrame = (word.startMs / 1000) * fps;

          const bounce = spring({
            frame: frame - wordStartFrame,
            fps,
            config: { damping: 10, mass: 0.5 },
          });

          const scale = isActive ? 0.95 + bounce * 0.05 : 1;

          return (
            <span
              key={`${i}-${word.text}`}
              style={{
                display: "inline-block",
                fontFamily: "Inter, Helvetica Neue, Arial, sans-serif",
                fontSize: 48,
                fontWeight: 800,
                lineHeight: 1.3,
                transform: `scale(${scale})`,
                color: isActive ? "#FFD700" : isPast ? "#FFFFFF" : "rgba(255,255,255,0.6)",
                backgroundColor: isActive
                  ? "rgba(0, 0, 0, 0.85)"
                  : "rgba(0, 0, 0, 0.6)",
                padding: "4px 10px",
                borderRadius: 6,
                textShadow: isActive
                  ? "0 0 8px rgba(255, 215, 0, 0.5)"
                  : "1px 2px 4px rgba(0, 0, 0, 0.8)",
              }}
            >
              {word.text}
            </span>
          );
        })}
      </div>
    </div>
  );
};
