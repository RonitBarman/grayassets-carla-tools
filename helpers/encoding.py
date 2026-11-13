import subprocess
import numpy as np

class FFmpegWriter:

    def __init__(self, output_prefix, width, height, fps=30):
        self.output_prefix = output_prefix
        self.fps = fps
        self.width = width
        self.height = height
        ffmpeg_cmd = ['ffmpeg', '-y', '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-s', f'{self.width}x{self.height}', '-r', str(self.fps), '-i', '-', '-c:v', 'libx264', '-preset', 'ultrafast', '-g', '10', f'{output_prefix}.mp4']
        self.process = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def write_frame(self, rgb_frame):
        self.process.stdin.write(rgb_frame.astype(np.uint8).tobytes())

    def close(self):
        if self.process.stdin:
            self.process.stdin.close()
        self.process.wait()

def combine_videos2gridview(output_folder, output_root="output"):
    """
    Combines four camera videos arranged in a 2x2 grid layout into a single video.

    Parameters:
        output_folder (str): The folder containing the input videos and where the combined video will be saved.

    Returns:
        str or None: The path to the combined video if successful; otherwise, None.
    """
    print('\nCombining all camera videos into a grid layout...')
    combined_output = f'{output_root}/{output_folder}/cameras_2x2_gridview.mp4'
    ffmpeg_cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-i', f'{output_root}/{output_folder}/video_camera1.mp4', '-i', f'{output_root}/{output_folder}/video_camera2.mp4', '-i', f'{output_root}/{output_folder}/video_camera3.mp4', '-i', f'{output_root}/{output_folder}/video_camera4.mp4', '-filter_complex', '[0:v][1:v]hstack=inputs=2[top];[2:v][3:v]hstack=inputs=2[bottom];[top][bottom]vstack=inputs=2', '-c:v', 'libx264', '-crf', '23', '-preset', 'veryfast', combined_output]
    try:
        subprocess.run(ffmpeg_cmd, check=True)
        print(f'    Combined video saved to: {combined_output}')
        return combined_output
    except subprocess.CalledProcessError as e:
        print(f'Error combining videos: {str(e)}')
        return None
    except Exception as e:
        print(f'Unexpected error during video combination: {str(e)}')
        return None
