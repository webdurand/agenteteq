import { Sequence, OffthreadVideo, Img } from "remotion";
import { ZoomPan, type MovementType } from "./ZoomPan";
import { Overlay } from "./Overlay";

export interface SceneData {
  name: string;
  narration: string;
  on_screen_text: string;
  overlay_animation?: "slide_up" | "scale_pop" | "fade_blur" | "slide_left";
  movement: MovementType;
  duration_s: number;
  scene_clip_url?: string;  // AI Motion: Kling I2V clip of the user in scenario
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
  // Determine background priority: Scene clip (I2V) > B-roll (T2V) > Talking head (D-ID) > Gradient
  const hasSceneClip = !!scene.scene_clip_url;
  const hasBroll = !!scene.broll_url;
  const hasTalkingHead = !!talkingHeadUrl;

  const coverStyle = {
    width: "100%" as const,
    height: "100%" as const,
    objectFit: "cover" as const,
  };

  return (
    <Sequence from={startFrame} durationInFrames={durationInFrames} name={scene.name}>
      <ZoomPan
        movement={scene.movement}
        durationInFrames={durationInFrames}
        startFrame={0}
      >
        {/* Background layer */}
        <div style={{ width: 1080, height: 1920, position: "relative" }}>
          {hasSceneClip ? (
            <OffthreadVideo src={scene.scene_clip_url!} style={coverStyle} />
          ) : hasBroll ? (
            <OffthreadVideo src={scene.broll_url!} style={coverStyle} />
          ) : hasTalkingHead ? (
            <OffthreadVideo src={talkingHeadUrl!} style={coverStyle} />
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
          animation={scene.overlay_animation || "slide_up"}
        />
      )}
    </Sequence>
  );
};
