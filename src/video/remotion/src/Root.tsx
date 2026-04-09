import { Composition } from "remotion";
import { ReelsVideo, type ReelsVideoProps } from "./compositions/ReelsVideo";

const defaultProps: ReelsVideoProps = {
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
};

export const Root: React.FC = () => {
  return (
    <>
      <Composition
        id="ReelsVideo"
        component={ReelsVideo as unknown as React.FC<Record<string, unknown>>}
        durationInFrames={30 * 60}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={defaultProps as unknown as Record<string, unknown>}
        calculateMetadata={({ props }: { props: Record<string, unknown> }) => {
          const p = props as unknown as ReelsVideoProps;
          let totalSeconds = (p.hook?.duration_s ?? 3);
          for (const scene of p.scenes ?? []) {
            totalSeconds += scene.duration_s ?? 5;
          }
          totalSeconds += (p.callback?.duration_s ?? 5);
          return {
            durationInFrames: Math.ceil(totalSeconds * 30),
          };
        }}
      />
    </>
  );
};
