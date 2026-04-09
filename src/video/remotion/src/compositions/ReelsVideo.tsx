import { AbsoluteFill, Audio, Sequence } from "remotion";
import type { Caption } from "@remotion/captions";
import { TikTokCaptions } from "../components/TikTokCaptions";
import { Scene, type SceneData } from "../components/Scene";
import { LoopBridge } from "../components/LoopBridge";

export interface ReelsVideoProps {
  /** URL of the full narration audio */
  audioUrl: string;
  /** Word-level captions from Whisper */
  captions: Caption[];
  /** Scenes from the script */
  scenes: SceneData[];
  /** Hook section */
  hook: {
    narration: string;
    on_screen_text: string;
    movement: string;
    duration_s: number;
    broll_url?: string;
  };
  /** Callback/ending section */
  callback: {
    narration: string;
    on_screen_text: string;
    movement: string;
    duration_s: number;
  };
  /** Global config */
  config: {
    music_url?: string;
    music_volume?: number;
    caption_style?: string;
    talking_head_url?: string;
  };
}

export const ReelsVideo: React.FC<ReelsVideoProps> = ({
  audioUrl,
  captions,
  scenes,
  hook,
  callback,
  config,
}) => {
  const fps = 30;
  const musicVolume = config.music_volume ?? 0.1;
  const talkingHeadUrl = config.talking_head_url;

  // Build timeline: hook + scenes + callback
  const allScenes: SceneData[] = [];

  // Hook as first scene
  allScenes.push({
    name: "hook",
    narration: hook.narration,
    on_screen_text: hook.on_screen_text,
    movement: (hook.movement as SceneData["movement"]) || "zoom_in_face",
    duration_s: hook.duration_s || 3,
    broll_url: hook.broll_url,
  });

  // Main scenes
  for (const scene of scenes) {
    allScenes.push(scene);
  }

  // Callback as last scene
  allScenes.push({
    name: "callback",
    narration: callback.narration,
    on_screen_text: callback.on_screen_text,
    movement: (callback.movement as SceneData["movement"]) || "zoom_out",
    duration_s: callback.duration_s || 5,
  });

  // Calculate frame positions
  let currentFrame = 0;
  const scenePositions = allScenes.map((scene) => {
    const start = currentFrame;
    const dur = Math.ceil((scene.duration_s || 5) * fps);
    currentFrame += dur;
    return { scene, startFrame: start, durationInFrames: dur };
  });

  return (
    <AbsoluteFill style={{ backgroundColor: "#000000" }}>
      <LoopBridge>
        {/* Scene layers */}
        {scenePositions.map(({ scene, startFrame, durationInFrames }, i) => (
          <Scene
            key={`${scene.name}-${i}`}
            scene={scene}
            startFrame={startFrame}
            durationInFrames={durationInFrames}
            talkingHeadUrl={talkingHeadUrl}
            fps={fps}
          />
        ))}

        {/* Narration audio */}
        {audioUrl && (
          <Sequence from={0}>
            <Audio src={audioUrl} volume={1} />
          </Sequence>
        )}

        {/* Background music */}
        {config.music_url && (
          <Sequence from={0}>
            <Audio src={config.music_url} volume={musicVolume} loop />
          </Sequence>
        )}

        {/* Dynamic captions layer (on top of everything) */}
        <AbsoluteFill style={{ zIndex: 100 }}>
          <TikTokCaptions captions={captions} />
        </AbsoluteFill>
      </LoopBridge>
    </AbsoluteFill>
  );
};
