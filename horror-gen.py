#!/usr/bin/env python3
"""
Horror Movie Quote Video Generator
A Python application to create YouTube Shorts from horror movie quotes
"""

import os
import sys
import time
import json
import random
import argparse
import requests
import asyncio
import tempfile
import subprocess
import re
import shutil
from io import BytesIO
from pathlib import Path
from typing import List, Tuple, Optional
from datetime import datetime

# Required external libraries
import openai
from PIL import Image, ImageDraw, ImageFont
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
from tqdm import tqdm

# Load environment variables
load_dotenv()

# Set up OpenAI client
openai_api_key = os.getenv("OPENAI_API_KEY")

# Configure dimensions for YouTube Shorts (9:16 aspect ratio)
WIDTH = 1080
HEIGHT = 1920

# Directory setup
QUOTES_DIR = Path("./quotes")
IMAGES_DIR = Path("./images")
FRAMES_DIR = Path("./frames")
OUTPUT_DIR = Path("./output")
AUDIO_DIR = Path("./audio")  # New directory for your custom audio files

def setup_directories() -> None:
    """Create necessary project directories and clean up quotes from previous runs"""
    for directory in [QUOTES_DIR, IMAGES_DIR, FRAMES_DIR, OUTPUT_DIR, AUDIO_DIR]:
        directory.mkdir(exist_ok=True, parents=True)
    
    # Clean up old quote files to ensure fresh quotes each run
    for quote_file in QUOTES_DIR.glob("quote_*.txt"):
        quote_file.unlink()
        print(f"Removed old quote file: {quote_file}")
    
    print("✓ Project directories created and cleaned")

def get_horror_movie_quotes(count: int = 9) -> List[str]:
    """
    Get horror movie quotes from ChatGPT with robust duplicate prevention
    
    Args:
        count: Number of quotes to retrieve
        
    Returns:
        List of horror movie quotes with movie titles
    """
    print(f"Requesting {count} horror movie quotes from ChatGPT...")
    
    # Check if we have a quotes history file
    history_file = Path("./quotes_history.txt")
    used_quotes = set()
    if history_file.exists():
        with open(history_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    # Normalize the quote to catch slight variations
                    normalized = line.lower().replace('"', '').replace("'", "").strip()
                    used_quotes.add(normalized)
    
    print(f"Found {len(used_quotes)} previously used quotes")
    
    # We'll make multiple attempts to get unique quotes
    max_attempts = 5
    all_new_quotes = []
    
    for attempt in range(max_attempts):
        if len(all_new_quotes) >= count:
            break
            
        print(f"Attempt {attempt + 1} to get unique quotes...")
        
        # Generate a random seed and timestamp to avoid caching
        random_seed = random.randint(1, 100000)
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
        
        # Create a more specific prompt for variety
        themes = ["classic horror", "modern horror", "psychological horror", "slasher films", "supernatural horror", "zombie films", "vampire movies", "ghost stories"]
        selected_theme = random.choice(themes)
        
        try:
            client = openai.OpenAI(api_key=openai_api_key)
            response = client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a film historian specializing in horror movies. Provide authentic, memorable quotes from horror films. Include only the quote and the movie title. Format as: 'QUOTE' - MOVIE TITLE (YEAR). Ensure each quote is unique and different from any you've provided before."
                    },
                    {
                        "role": "user",
                        "content": f"Provide {count - len(all_new_quotes)} different, authentic horror movie quotes focusing on {selected_theme}. Choose quotes that are impactful, memorable, and would look good on a dramatic background. Random seed: {random_seed}, timestamp: {timestamp}. Make sure these are completely different from typical horror quotes and avoid common, overused lines."
                    }
                ],
                temperature=1.0,
                top_p=0.9,
            )
            
            # Extract quotes from response
            content = response.choices[0].message.content
            quote_lines = [line.strip() for line in content.split('\n') if line.strip()]
            
            # Check each quote for uniqueness
            for quote in quote_lines:
                if len(all_new_quotes) >= count:
                    break
                    
                # Normalize for comparison
                normalized = quote.lower().replace('"', '').replace("'", "").strip()
                
                # Check if this quote is unique
                if normalized not in used_quotes:
                    all_new_quotes.append(quote)
                    used_quotes.add(normalized)
                    print(f"  ✓ Added unique quote: {quote[:50]}...")
                else:
                    print(f"  × Skipped duplicate: {quote[:50]}...")
            
        except Exception as e:
            print(f"Error in attempt {attempt + 1}: {e}")
            continue
    
    if len(all_new_quotes) < count:
        print(f"Warning: Only got {len(all_new_quotes)} unique quotes out of {count} requested")
    
    # Store quotes to files
    for i, quote in enumerate(all_new_quotes):
        with open(QUOTES_DIR / f"quote_{i + 1}.txt", "w", encoding='utf-8') as f:
            f.write(quote)
    
    # Add new quotes to history file (avoiding duplicates)
    if all_new_quotes:
        with open(history_file, 'a', encoding='utf-8') as f:
            for quote in all_new_quotes:
                f.write(f"{quote}\n")
    
    print(f"✓ Generated {len(all_new_quotes)} unique horror movie quotes")
    return all_new_quotes

def generate_background_image(quote: str, index: int) -> Path:
    """
    Generate a creepy background image for a quote
    
    Args:
        quote: The horror movie quote
        index: Quote index for filename
        
    Returns:
        Path to the generated image
    """
    print(f"GENERATING BACKGROUND for quote {index + 1}...")
    print(f"THIS FUNCTION IS DEFINITELY RUNNING NOW")
    
    try:
        # Create a dark gradient background
        image_path = IMAGES_DIR / f"background_{index + 1}.png"
        
        # Create gradient from one dark color to another
        # Use a different color combination for each image to add variety
        color_pairs = [
            ((120, 0, 0), (40, 0, 0)),     # Dark red gradient (brighter)
            ((0, 0, 120), (0, 0, 40)),     # Dark blue gradient (brighter)
            ((80, 0, 100), (30, 0, 40)),   # Dark purple gradient (brighter)
            ((0, 80, 80), (0, 30, 30)),    # Dark teal gradient (brighter)
            ((100, 80, 0), (40, 30, 0)),   # Dark amber gradient (brighter)
            ((80, 80, 80), (30, 30, 30)),  # Dark gray gradient (brighter)
            ((0, 100, 0), (0, 40, 0)),     # Dark green gradient (brighter)
            ((100, 0, 100), (40, 0, 40)),  # Dark magenta gradient (brighter)
            ((100, 50, 0), (40, 20, 0))    # Dark orange gradient (brighter)
        ]
        
        # Select a color pair based on the index, cycling through options
        color1, color2 = color_pairs[index % len(color_pairs)]
        print(f"Using color gradient: {color1} to {color2}")
        
        # Create a new image with the 9:16 aspect ratio
        img = Image.new('RGB', (WIDTH, HEIGHT), color=color1)
        draw = ImageDraw.Draw(img)
        
        print(f"Drawing gradient...")
        # Draw the gradient background
        for y in range(HEIGHT):
            # Calculate color for this row
            r = int(color1[0] + (color2[0] - color1[0]) * y / HEIGHT)
            g = int(color1[1] + (color2[1] - color1[1]) * y / HEIGHT)
            b = int(color1[2] + (color2[2] - color1[2]) * y / HEIGHT)
            
            # Draw a line with this color
            draw.line([(0, y), (WIDTH, y)], fill=(r, g, b))
        
        # Save the background image (before adding texture to ensure it works)
        img.save(image_path)
        print(f"Saved basic gradient to {image_path}")
        
        # Add some random dark texture elements for a creepy effect
        try:
            print(f"Adding texture elements...")
            texture_img = img.copy().convert('RGBA')
            texture_draw = ImageDraw.Draw(texture_img)
            
            for _ in range(100):  # Reduced number for speed
                x = random.randint(0, WIDTH)
                y = random.randint(0, HEIGHT)
                size = random.randint(5, 100)
                
                # Draw directly on the image without using a separate shape
                texture_draw.ellipse(
                    (x - size, y - size, x + size, y + size), 
                    fill=(0, 0, 0, random.randint(0, 50))
                )
            
            # Convert back to RGB and save
            final_img = texture_img.convert('RGB')
            final_img.save(image_path)
            print(f"Saved textured gradient to {image_path}")
            
        except Exception as texture_error:
            print(f"Error adding texture: {texture_error}, using basic gradient")
            # The basic gradient was already saved, so we'll use that
        
        print(f"✓ Created background image {index + 1} at {image_path}")
        # Verify it exists
        if os.path.exists(image_path):
            print(f"Verified background image file exists: {image_path}")
            # Check file size to ensure it's not empty
            file_size = os.path.getsize(image_path)
            print(f"Background file size: {file_size} bytes")
            if file_size < 1000:
                print("WARNING: Background file is suspiciously small!")
        else:
            print(f"WARNING: Background image file not found after saving: {image_path}")
        
        return image_path
        
    except Exception as e:
        print(f"Error generating background image: {e}")
        # Return a simple black background if something goes wrong
        image_path = IMAGES_DIR / f"background_{index + 1}.png"
        Image.new("RGB", (WIDTH, HEIGHT), color="black").save(image_path)
        print(f"  Created black placeholder image instead")
        return image_path

def create_frames_with_text(background_paths: List[Path], quotes: List[str]) -> List[Path]:
    """
    Create frames with large text on gradient backgrounds
    
    Args:
        background_paths: Paths to background images
        quotes: List of quotes
        
    Returns:
        List of paths to frames with text
    """
    print("Creating frames with large text on gradient backgrounds...")
    
    frame_paths = []
    for i, (background_path, quote) in enumerate(zip(background_paths, quotes)):
        print(f"Creating frame {i+1}...")
        print(f"Using background: {background_path}")
        
        try:
            # Split quote into quote text and movie title
            parts = quote.split(' - ')
            quote_text = parts[0].strip()
            movie_title = parts[1].strip() if len(parts) > 1 else "Unknown"
            
            # Remove leading numbers from quote (e.g., "1. " or "1) ")
            quote_text = re.sub(r'^\d+[\.\)]\s*', '', quote_text)
            
            # Output path for the frame
            frame_path = FRAMES_DIR / f"frame_{i + 1}.png"
            
            # SANITY CHECK - create a solid color background instead of using the gradient
            # This is to see if the issue is with loading the gradient or with something else
            bg_type = "gradient"  # "gradient" to use the gradient, "solid" to use a solid color
            
            if bg_type == "solid":
                # Create a solid color background (for testing)
                colors = [(120, 0, 0), (0, 0, 120), (0, 120, 0), (120, 0, 120), (0, 120, 120)]
                img = Image.new('RGB', (WIDTH, HEIGHT), colors[i % len(colors)])
                print(f"Created solid color background: {colors[i % len(colors)]}")
            else:
                # Use the gradient background
                try:
                    # Check if the file exists and print debug info
                    if not os.path.isfile(background_path):
                        print(f"Background file not found: {background_path}")
                        raise FileNotFoundError(f"Background file not found: {background_path}")
                    else:
                        print(f"Background file exists: {background_path}")
                        # Print file size
                        file_size = os.path.getsize(background_path)
                        print(f"Background file size: {file_size} bytes")
                        
                    # Load the background
                    bg = Image.open(background_path)
                    print(f"Background loaded with size: {bg.size}, mode: {bg.mode}")
                    
                    # Resize if needed
                    if bg.size != (WIDTH, HEIGHT):
                        bg = bg.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
                        
                    # Add a semi-transparent black overlay for readability
                    if bg.mode != 'RGBA':
                        bg = bg.convert('RGBA')
                    
                    overlay = Image.new('RGBA', (WIDTH, HEIGHT), (0, 0, 0, 100))
                    img = Image.alpha_composite(bg, overlay).convert('RGB')
                    
                    print(f"Background processed successfully")
                    
                except Exception as bg_error:
                    print(f"Background error: {bg_error}. Using plain black background.")
                    img = Image.new('RGB', (WIDTH, HEIGHT), color=(0, 0, 0))
            
            # Create a drawing object
            draw = ImageDraw.Draw(img)
            
            # Try to load a system font if available, otherwise use default
            try:
                # Try to find a system font
                system_fonts = [
                    '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
                    '/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf',
                    '/Library/Fonts/Arial Bold.ttf',
                    '/Library/Fonts/Helvetica.ttc',
                    'C:\\Windows\\Fonts\\arialbd.ttf',
                    'C:\\Windows\\Fonts\\segoeui.ttf'
                ]
                
                quote_font = None
                movie_font = None
                
                for font_path in system_fonts:
                    if os.path.exists(font_path):
                        quote_font = ImageFont.truetype(font_path, 60)  # Very large font for quotes
                        movie_font = ImageFont.truetype(font_path, 48)  # Large font for movie title
                        print(f"Using system font: {font_path}")
                        break
                
                if not quote_font:
                    # Fall back to default font
                    quote_font = ImageFont.load_default()
                    movie_font = ImageFont.load_default()
                    print("Using default font")
            except Exception as font_error:
                print(f"Font loading error: {font_error}")
                quote_font = ImageFont.load_default()
                movie_font = ImageFont.load_default()
            
            # Break the quote into lines
            words = quote_text.split()
            lines = []
            current_line = []
            
            # Keep lines shorter for better readability
            max_chars_per_line = 25
            
            for word in words:
                if len(' '.join(current_line + [word])) <= max_chars_per_line:
                    current_line.append(word)
                else:
                    if current_line:
                        lines.append(' '.join(current_line))
                        current_line = [word]
                    else:
                        lines.append(word)
            
            if current_line:
                lines.append(' '.join(current_line))
            
            # Write each line of the quote
            y = HEIGHT // 4
            for line in lines:
                # Draw white text on background
                text_width = draw.textlength(line, font=quote_font) if hasattr(draw, 'textlength') else len(line) * 30
                x = (WIDTH - text_width) // 2
                
                # Single draw with large font
                draw.text((x, y), line, fill=(255, 255, 255), font=quote_font)
                
                y += 100  # Large line spacing
            
            # Write movie title
            y = HEIGHT * 3 // 4
            movie_text = f"- {movie_title}"
            text_width = draw.textlength(movie_text, font=movie_font) if hasattr(draw, 'textlength') else len(movie_text) * 24
            x = (WIDTH - text_width) // 2
            
            # Draw movie title
            draw.text((x, y), movie_text, fill=(255, 255, 255), font=movie_font)
            
            # Save the image
            img.save(frame_path)
            frame_paths.append(frame_path)
            
            print(f"✓ Created frame {i+1}")
            
        except Exception as e:
            print(f"Error creating frame {i+1}: {e}")
            print(f"Exception details: {type(e).__name__}: {str(e)}")
            # Create a simple error frame
            frame_path = FRAMES_DIR / f"frame_{i + 1}.png"
            error_img = Image.new('RGB', (WIDTH, HEIGHT), color="black")
            error_draw = ImageDraw.Draw(error_img)
            error_draw.text((WIDTH//2, HEIGHT//2), f"Error: {str(e)}", fill="white")
            error_img.save(frame_path)
            frame_paths.append(frame_path)
    
    return frame_paths

def create_video(frame_paths: List[Path], output_path: Path, music_path: Optional[Path] = None, duration_per_frame: int = 10) -> Path:
    """
    Combine frames into a video using FFmpeg directly with separate audio handling
    
    Args:
        frame_paths: List of paths to the frames with text
        output_path: Path for the output video
        music_path: Optional path to background music
        duration_per_frame: Duration in seconds for each frame
        
    Returns:
        Path to the created video
    """
    print("\n" + "="*50)
    print("VIDEO CREATION WITH CUSTOM AUDIO")
    print("="*50)
    
    try:
        # Convert all paths to absolute paths to avoid FFmpeg path issues
        abs_frame_paths = [os.path.abspath(str(path)) for path in frame_paths]
        abs_output_path = os.path.abspath(str(output_path))
        abs_music_path = os.path.abspath(str(music_path)) if music_path else None
        
        # Create a temp dir for intermediate files
        temp_dir = tempfile.mkdtemp()
        print(f"Created temp directory: {temp_dir}")
        
        print(f"Creating video with {len(frame_paths)} frames, duration: {duration_per_frame}s each")
        print(f"Output will be saved to: {abs_output_path}")
        
        # Debug music path
        has_valid_audio = False
        if music_path and os.path.exists(abs_music_path):
            file_size = os.path.getsize(abs_music_path)
            print(f"Music file exists, size: {file_size} bytes")
            
            # Try to check if it's a valid audio file
            try:
                audio_check_cmd = ['ffprobe', '-v', 'error', '-i', abs_music_path]
                check_result = subprocess.run(audio_check_cmd, capture_output=True, text=True)
                if check_result.returncode == 0:
                    print("Audio file validation successful!")
                    has_valid_audio = True
                else:
                    print(f"Audio validation failed: {check_result.stderr}")
            except Exception as check_error:
                print(f"Error checking audio file: {check_error}")
        else:
            print("No valid music path provided")
        
        # STEP 1: Create a silent video from frames
        print("\nStep 1: Creating silent video from frames...")
        
        # Create a temporary text file with frame information for ffmpeg
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            frame_list_path = f.name
            # Write each frame with its duration using absolute paths
            for frame_path in abs_frame_paths:
                f.write(f"file '{frame_path}'\n")
                f.write(f"duration {duration_per_frame}\n")
            # Write the last frame again without duration (required by ffmpeg)
            if abs_frame_paths:
                f.write(f"file '{abs_frame_paths[-1]}'\n")
        
        silent_video_path = os.path.join(temp_dir, "silent_video.mp4")
        
        # Basic FFmpeg command for silent video
        silent_cmd = [
            'ffmpeg',
            '-y',                   # Overwrite output file if it exists
            '-f', 'concat',         # Use concat demuxer
            '-safe', '0',           # Don't check for relative paths
            '-i', frame_list_path,  # Input file list
            '-c:v', 'libx264',      # Video codec
            '-pix_fmt', 'yuv420p',  # Pixel format for compatibility
            '-preset', 'medium',    # Encoding speed/compression tradeoff
            '-crf', '23',           # Quality (lower is better)
            '-r', '30',             # Output frame rate
            silent_video_path
        ]
        
        print(f"Running command: {' '.join(silent_cmd)}")
        
        silent_result = subprocess.run(silent_cmd, capture_output=True, text=True)
        if silent_result.returncode != 0:
            print(f"Error creating silent video: {silent_result.stderr}")
            raise Exception("Failed to create silent video")
        
        print(f"Silent video created: {silent_video_path}")
        
        # STEP 2: Add audio if available
        final_video_path = abs_output_path
        
        if has_valid_audio:
            print("\nStep 2: Adding custom audio to video...")
            
            # Calculate total video duration
            total_duration = len(frame_paths) * duration_per_frame
            
            # FFmpeg command to add audio
            audio_cmd = [
                'ffmpeg',
                '-y',                      # Overwrite output file if it exists
                '-i', silent_video_path,   # Input video
                '-i', abs_music_path,      # Input audio
                '-c:v', 'copy',            # Copy video without re-encoding
                '-c:a', 'aac',             # Audio codec
                '-b:a', '192k',            # Audio bitrate
                '-shortest',               # End when shortest input ends
                final_video_path
            ]
            
            print(f"Running command: {' '.join(audio_cmd)}")
            
            audio_result = subprocess.run(audio_cmd, capture_output=True, text=True)
            if audio_result.returncode != 0:
                print(f"Error adding audio: {audio_result.stderr}")
                # If audio fails, just use the silent video
                print("Using silent video as fallback")
                final_video_path = silent_video_path
            else:
                print("Audio added successfully!")
                
                # Verify the output video has audio
                try:
                    verify_cmd = [
                        'ffprobe',
                        '-v', 'error',
                        '-select_streams', 'a:0',
                        '-show_entries', 'stream=codec_type',
                        '-of', 'csv=p=0',
                        final_video_path
                    ]
                    verify_result = subprocess.run(verify_cmd, capture_output=True, text=True)
                    if 'audio' in verify_result.stdout:
                        print("✓ Output video contains audio!")
                    else:
                        print("WARNING: Output video does not contain audio!")
                except Exception as verify_err:
                    print(f"Error verifying audio: {verify_err}")
        else:
            print("\nStep 2: No valid audio - using silent video")
            final_video_path = silent_video_path
        
        # STEP 3: Copy the result to the desired output location if needed
        if final_video_path != abs_output_path:
            print(f"\nStep 3: Copying video to final destination: {abs_output_path}")
            shutil.copy2(final_video_path, abs_output_path)
        
        # Clean up temporary files
        try:
            shutil.rmtree(temp_dir)
            os.unlink(frame_list_path)
            print("Temporary files cleaned up")
        except Exception as clean_error:
            print(f"Error cleaning up temp files: {clean_error}")
        
        # Final verification
        if os.path.exists(abs_output_path):
            file_size = os.path.getsize(abs_output_path)
            print(f"\n✓ Final video created at {abs_output_path}, size: {file_size} bytes")
            return output_path
        else:
            print(f"ERROR: Final video not found at {abs_output_path}")
            raise Exception("Final video not created")
        
    except Exception as e:
        print(f"ERROR: Failed to create video: {e}")
        import traceback
        print(f"Stack trace: {traceback.format_exc()}")
        sys.exit(1)
    finally:
        print("="*50 + "\n")

def get_custom_audio() -> Optional[Path]:
    """
    Check for custom audio files in the audio directory
    
    Returns:
        Path to a custom audio file or None if not found
    """
    print("\nLooking for custom audio files in the audio directory...")
    
    # Create the audio directory if it doesn't exist
    AUDIO_DIR.mkdir(exist_ok=True)
    
    # Check if there are any audio files in the directory
    audio_files = list(AUDIO_DIR.glob('*.mp3')) + list(AUDIO_DIR.glob('*.wav')) + list(AUDIO_DIR.glob('*.m4a'))
    
    if not audio_files:
        print("No custom audio files found in the audio directory.")
        print(f"Please place your MP3, WAV, or M4A files in the {AUDIO_DIR} directory.")
        return None
    
    # If there are multiple files, select one randomly or the first one
    selected_audio = random.choice(audio_files)
    print(f"Selected audio file: {selected_audio}")
    
    # Verify the audio file
    try:
        file_size = os.path.getsize(selected_audio)
        print(f"Audio file size: {file_size} bytes")
        
        # Check if it's a valid audio file
        audio_check_cmd = ['ffprobe', '-v', 'error', '-i', str(selected_audio)]
        check_result = subprocess.run(audio_check_cmd, capture_output=True, text=True)
        if check_result.returncode == 0:
            print("✓ Audio file validation successful")
            return selected_audio
        else:
            print(f"Audio validation failed: {check_result.stderr}")
            return None
    except Exception as e:
        print(f"Error checking audio file: {e}")
        return None

def get_youtube_credentials() -> Credentials:
    """
    Get or refresh YouTube API credentials
    
    Returns:
        Google credentials for YouTube API
    """
    # YouTube API OAuth 2.0 scopes
    SCOPES = ['https://www.googleapis.com/auth/youtube.upload']
    
    creds = None
    token_path = Path('token.json')
    
    # Check if token.json exists
    if token_path.exists():
        try:
            creds = Credentials.from_authorized_user_info(
                json.loads(token_path.read_text()), SCOPES)
        except Exception:
            pass
    
    # If credentials don't exist or are invalid, get new ones
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                # Need new credentials
                creds = None
        
        # If still no valid credentials, need to go through OAuth flow
        if not creds:
            client_secrets_path = Path('client_secret.json')
            
            if not client_secrets_path.exists():
                print("Error: client_secret.json not found.")
                print("Download it from Google Cloud Console and save it as client_secret.json")
                sys.exit(1)
            
            flow = InstalledAppFlow.from_client_secrets_file(
                str(client_secrets_path), SCOPES)
            creds = flow.run_local_server(port=0)
        
        # Save the credentials for the next run
        with open(token_path, 'w') as token:
            token.write(creds.to_json())
    
    return creds

def upload_to_youtube(video_path: Path, title: str, description: str, tags: List[str]) -> str:
    """
    Upload video to YouTube as a Short
    
    Args:
        video_path: Path to video file
        title: Video title
        description: Video description
        tags: List of tags
        
    Returns:
        YouTube video ID
    """
    print("Preparing to upload to YouTube...")
    
    try:
        # Get credentials
        creds = get_youtube_credentials()
        
        # Build YouTube API service
        youtube = build('youtube', 'v3', credentials=creds)
        
        # Prepare video metadata
        body = {
            'snippet': {
                'title': title,
                'description': description,
                'tags': tags,
                'categoryId': '17'  # Entertainment category
            },
            'status': {
                'privacyStatus': 'private',  # Start private, can change to public later
                'selfDeclaredMadeForKids': False
            }
        }
        
        # Upload video
        print("Uploading video to YouTube (this may take a while)...")
        media = MediaFileUpload(
            str(video_path),
            mimetype='video/mp4',
            resumable=True
        )
        
        request = youtube.videos().insert(
            part='snippet,status',
            body=body,
            media_body=media
        )
        
        response = request.execute()
        video_id = response['id']
        
        print(f"✓ Video uploaded to YouTube: https://www.youtube.com/watch?v={video_id}")
        return video_id
        
    except HttpError as e:
        print(f"YouTube API error: {e.reason}")
        print("Check your credentials and try again.")
        sys.exit(1)
        
    except Exception as e:
        print(f"Error uploading to YouTube: {e}")
        sys.exit(1)

async def main():
    """Main function to run the horror quote video generator"""
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Horror Movie Quote Video Generator')
    parser.add_argument('--quotes', type=int, default=9, help='Number of quotes to use (default: 9)')
    parser.add_argument('--duration', type=int, default=10, help='Duration per quote in seconds (default: 10)')
    parser.add_argument('--upload', action='store_true', help='Upload to YouTube when done')
    parser.add_argument('--custom-audio', action='store_true', help='Use custom audio from the audio directory', default=True)
    parser.add_argument('--audio-file', type=str, help='Specific audio file to use (place in audio directory)')
    args = parser.parse_args()
    
    print("=" * 60)
    print(" HORROR MOVIE QUOTE VIDEO GENERATOR ")
    print("=" * 60)
    
    # Setup directories
    setup_directories()
    
    # Generate quotes
    quotes = get_horror_movie_quotes(args.quotes)
    
    # Generate background images
    background_paths = []
    for i, quote in enumerate(quotes):
        background_path = generate_background_image(quote, i)
        print(f"Generated background at: {background_path}, exists: {os.path.exists(background_path)}")
        background_paths.append(background_path)
    
    # Create frames with text using the background images
    frame_paths = create_frames_with_text(background_paths, quotes)
    
    # Check for custom audio
    music_path = None
    if args.audio_file:
        # Use specific audio file if provided
        specific_file = AUDIO_DIR / args.audio_file
        if os.path.exists(specific_file):
            music_path = specific_file
            print(f"Using specified audio file: {music_path}")
        else:
            print(f"Specified audio file not found: {args.audio_file}")
            print(f"Please place the file in the {AUDIO_DIR} directory.")
    elif args.custom_audio:
        # Use any available custom audio
        music_path = get_custom_audio()
        if not music_path:
            print("No custom audio files found. Please add MP3, WAV, or M4A files to the audio directory.")
            
    # Generate timestamp for unique filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = OUTPUT_DIR / f"horror_quotes_{timestamp}.mp4"
    
    # Create video
    video_path = create_video(
        frame_paths, 
        output_path, 
        music_path, 
        duration_per_frame=args.duration
    )
    
    # Optional: Upload to YouTube
    if args.upload:
        try:
            video_id = upload_to_youtube(
                video_path,
                'Haunting Horror Movie Quotes',
                'A collection of the most spine-chilling quotes from classic horror films',
                ['horror', 'movie quotes', 'scary', 'horror films', 'shorts']
            )
        except Exception as e:
            print(f"YouTube upload failed: {e}")
            print(f"Your video is still available locally at: {video_path}")
    
    print("=" * 60)
    print(f"✓ Process completed successfully!")
    print(f"✓ Video saved to: {video_path}")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(main())
