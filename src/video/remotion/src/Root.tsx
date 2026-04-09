import { Composition } from "remotion";
import { ReelsVideo, type ReelsVideoProps } from "./compositions/ReelsVideo";

export const Root: React.FC = () => {
  return (
    <>
      <Composition
        id="ReelsVideo"
        component={ReelsVideo}
        durationInFrames={30 * 60} // default 60s at 30fps, overridden by calculateMetadata
        fps={30}
        width={1080}
        height={1920}
        defaultProps={
          {
            audioUrl: "",
            captions: [],
            scenes: [],
            hook: {
              narration: "",
              on_screen_text: "",
              movement: "zoom_in_face",
              duration_s: 3,
            },
            callback: {
              narration: "",
              on_screen_text: "",
              movement: "zoom_out",
              duration_s: 5,
            },
            config: {
              music_url: "",
              music_volume: 0.1,
              caption_style: "tiktok_bounce_highlight",
            },
          } satisfies ReelsVideoProps
        }
        calculateMetadata={({ props }) => {
          // Calculate total duration from scenes
          let totalSeconds = (props.hook?.duration_s ?? 3);
          for (const scene of props.scenes ?? []) {
            totalSeconds += scene.duration_s ?? 5;
          }
          totalSeconds += (props.callback?.duration_s ?? 5);
          return {
            durationInFrames: Math.ceil(totalSeconds * 30),
          };
        }}
      />
    </>
  );
};
