For details about this project, check out: [https://billford.io/@billfordx/vibing-my-way-to-horror-happiness-509432505faf](https://billford.io/vibing-my-way-to-horror-happiness-509432505faf)

# Horror Movie Quote Video Generator

A Python application that creates YouTube Shorts-style videos from horror movie quotes with custom background music. The script generates atmospheric gradient backgrounds, overlays memorable horror movie quotes, and combines everything into vertical videos perfect for social media.

## Features

- **AI-Generated Quotes**: Uses OpenAI's GPT-4 to generate authentic horror movie quotes
- **Custom Audio**: Use your own MP3, WAV, or M4A files for background music
- **Duplicate Prevention**: Advanced system to ensure no repeated quotes across runs
- **Atmospheric Visuals**: Creates dark gradient backgrounds with texture effects
- **YouTube Shorts Ready**: Outputs videos in 9:16 aspect ratio (1080x1920)
- **Customizable**: Adjust number of quotes, duration, and specific audio files
- **YouTube Upload**: Optional direct upload to YouTube (requires API setup)

## Sample Output

The generator creates videos with:
- Dark, atmospheric gradient backgrounds in various color schemes
- Large, readable white text displaying horror movie quotes
- Movie title attribution at the bottom
- Your custom background music
- Professional-quality output suitable for social media

## Requirements

- Python 3.7+
- OpenAI API key
- FFmpeg (for video processing)
- Various Python packages (see INSTALL.md)

## Quick Start

1. Follow the installation instructions in `INSTALL.md`
2. Set up your OpenAI API key in a `.env` file
3. Place your audio files in the `./audio` directory
4. Run the script:

```bash
python3 horror_movie_quote_generator.py
```

## Usage

### Basic Usage
```bash
# Generate a video with default settings (9 quotes, 10 seconds each)
python3 horror_movie_quote_generator.py

# Generate 5 quotes with 8 seconds each
python3 horror_movie_quote_generator.py --quotes 5 --duration 8

# Use a specific audio file
python3 horror_movie_quote_generator.py --audio-file spooky_music.mp3
```

### Command-Line Options

- `--quotes N`: Number of quotes to generate (default: 9)
- `--duration N`: Duration per quote in seconds (default: 10)
- `--upload`: Upload finished video to YouTube
- `--custom-audio`: Use custom audio files (enabled by default)
- `--audio-file FILENAME`: Specify a particular audio file from the audio directory

### Examples

```bash
# Create a short 30-second video (3 quotes × 10 seconds)
python3 horror_movie_quote_generator.py --quotes 3

# Create a longer video with specific audio
python3 horror_movie_quote_generator.py --quotes 12 --duration 15 --audio-file my_horror_soundtrack.mp3

# Generate and upload to YouTube
python3 horror_movie_quote_generator.py --upload
```

## Directory Structure

After running the script, you'll have:

```
./
├── horror_movie_quote_generator.py
├── .env
├── audio/                    # Your custom audio files
│   ├── horror_music.mp3
│   └── creepy_sounds.wav
├── quotes/                   # Generated quote text files
├── images/                   # Generated background images
├── frames/                   # Final frames with text overlay
├── output/                   # Final video files
└── quotes_history.txt        # Tracks used quotes to prevent repeats
```

## Custom Audio

The script looks for audio files in the `./audio` directory. Supported formats:
- MP3
- WAV
- M4A

If multiple audio files are present, one will be randomly selected. You can specify a particular file using the `--audio-file` option.

## YouTube Upload (Optional)

To enable YouTube uploads:

1. Set up a Google Cloud Project with YouTube Data API v3 enabled
2. Download the client secrets file as `client_secret.json`
3. Place it in the same directory as the script
4. Run with the `--upload` flag

The first time you upload, you'll need to authorize the application through your web browser.

## Troubleshooting

### Common Issues

**No quotes generated**: Check your OpenAI API key in the `.env` file
**No audio in video**: Ensure audio files are in the `./audio` directory and have correct extensions
**FFmpeg errors**: Make sure FFmpeg is properly installed with audio codec support
**Font issues**: The script will fall back to default fonts if system fonts aren't found

### Quote Repetition

The script maintains a history file (`quotes_history.txt`) to prevent repeated quotes. If you want to reset and allow previously used quotes:

```bash
rm quotes_history.txt
```

### Debugging

The script provides detailed logging. Look for:
- Quote generation attempts and successes
- Audio file validation messages
- Video creation progress
- Any error messages with specific details

## Contributing

Feel free to submit issues, feature requests, or pull requests. Some ideas for improvements:

- Additional background image styles
- More text formatting options
- Different video aspect ratios
- Batch processing multiple videos
- Integration with other AI models

## License

This project is open source. Please ensure you comply with OpenAI's usage policies and respect copyright when using movie quotes.

## Disclaimer

This tool generates videos using movie quotes for entertainment purposes. Ensure you have proper rights for any commercial use of the generated content.
