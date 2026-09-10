def compress_video(input_file, output_file):
    command = [
        "ffmpeg",
        "-y",
        "-i", input_file,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "28",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        output_file
    ]

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        # Haqiqiy FFmpeg xatosini logga chiqaramiz
        logging.error("FFMPEG ERROR:\n%s", result.stderr)
        raise Exception(
            "FFmpeg: " + result.stderr[-1500:]
        )
