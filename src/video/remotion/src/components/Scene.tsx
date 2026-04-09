import { Sequence, OffthreadVideo, Img } from "remotion";
import { ZoomPan, type MovementType } from "./ZoomPan";
import { Overlay } from "./Overlay";

export interface SceneData {
  name: string;
  narration: string;
  on_screen_text: string;
  movement: MovementType;
  duration_s: number;
  broll_url?: string;
  overlay_image_url?: string;
  sfx?: string;
}

interface SceneProps {
  scene: SceneData;
  startFrame: number;
  durationInFrames: number;
  talkingHeadUrl?: string;
  fps: number;
}

export const Scene: React.FC<SceneProps> = ({
  scene,
  startFrame,
  durationInFrames,
  talkingHeadUrl,
  fps,
}) => {
  // Determine background: B-roll video > talking head > solid color
  const hasBroll = !!scene.broll_url;
  const hasTalkingHead = !!talkingHeadUrl;

  return (
    <Sequence from={startFrame} durationInFrames={durationInFrames} name={scene.name}>
      <ZoomPan
        movement={scene.movement}
        durationInFrames={durationInFrames}
        startFrame={0}
      >
        {/* Background layer */}
        <div style={{ width: 1080, height: 1920, position: "relative" }}>
          {hasBroll ? (
            <OffthreadVideo
              src={scene.broll_url!}
              style={{
                width: "100%",
                height: "100%",
                objectFit: "cover",
              }}
            />
          ) : hasTalkingHead ? (
            <OffthreadVideo
              src={talkingHeadUrl!}
              style={{
                width: "100%",
                height: "100%",
                objectFit: "cover",
              }}
            />
          ) : (
            <div
              style={{
                width: "100%",
                height: "100%",
                background: "linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%)",
              }}
            />
          )}
        </div>
      </ZoomPan>

      {/* On-screen text overlay */}
      {scene.on_screen_text && (
        <Overlay
          text={scene.on_screen_text}
          startFrame={0}
          durationInFrames={durationInFrames}
          position="top"
          imageUrl={scene.overlay_image_url}
        />
      )}
    </Sequence>
  );
};
