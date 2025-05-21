# Installation Guide - Horror Movie Quote Video Generator

This guide will walk you through setting up the Horror Movie Quote Video Generator on your system.

## System Requirements

- **Operating System**: Linux, macOS, or Windows
- **Python**: Version 3.7 or higher
- **RAM**: At least 2GB available
- **Storage**: 500MB+ free space for generated content
- **Internet**: Required for OpenAI API calls

## Prerequisites

### 1. Python Installation

#### Ubuntu/Debian:
```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv
```

#### CentOS/RHEL/Rocky Linux:
```bash
sudo yum install python3 python3-pip
# or for newer versions:
sudo dnf install python3 python3-pip
```

#### macOS:
```bash
# Using Homebrew
brew install python3

# Or download from python.org
```

#### Windows:
Download Python from [python.org](https://www.python.org/downloads/) and install it.

### 2. FFmpeg Installation

FFmpeg is required for video processing.

#### Ubuntu/Debian:
```bash
sudo apt update
sudo apt install ffmpeg
```

#### CentOS/RHEL/Rocky Linux:
```bash
# Enable EPEL repository first
sudo yum install epel-release
sudo yum install ffmpeg

# For newer versions:
sudo dnf install ffmpeg
```

#### macOS:
```bash
# Using Homebrew
brew install ffmpeg
```

#### Windows:
1. Download FFmpeg from [ffmpeg.org](https://ffmpeg.org/download.html)
2. Extract to a folder (e.g., `C:\ffmpeg`)
3. Add the `bin` folder to your system PATH

### 3. Verify Installation

Test that everything is installed correctly:

```bash
python3 --version
pip3 --version
ffmpeg -version
```

## Project Setup

### 1. Download the Script

Save the `horror_movie_quote_generator.py` script to your desired directory.

### 2. Create Virtual Environment (Recommended)

```bash
# Navigate to your project directory
cd /path/to/your/project

# Create virtual environment
python3 -m venv horror_quotes_env

# Activate virtual environment
# On Linux/macOS:
source horror_quotes_env/bin/activate

# On Windows:
# horror_quotes_env\Scripts\activate
```

### 3. Install Python Dependencies

```bash
pip install openai pillow python-dotenv google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client tqdm
```

Or if you prefer to install them individually:

```bash
pip install openai                    # OpenAI API client
pip install pillow                    # Image processing
pip install python-dotenv            # Environment variable loading
pip install google-auth              # Google authentication
pip install google-auth-oauthlib     # OAuth for Google APIs
pip install google-auth-httplib2     # HTTP library for Google APIs
pip install google-api-python-client # YouTube API client
pip install tqdm                     # Progress bars
```

### 4. Set Up OpenAI API Key

#### Get an OpenAI API Key:
1. Go to [platform.openai.com](https://platform.openai.com/)
2. Sign up or log in
3. Navigate to API Keys section
4. Create a new API key
5. Copy the key (you won't be able to see it again)

#### Create Environment File:
Create a `.env` file in your project directory:

```bash
# Create the .env file
touch .env

# Edit it with your preferred editor
nano .env
```

Add your API key to the `.env` file:
```
OPENAI_API_KEY=your_api_key_here
```

**Important**: Never commit this file to version control or share it publicly.

### 5. Create Directory Structure

The script will create these automatically, but you can create them manually if desired:

```bash
mkdir -p audio quotes images frames output
```

### 6. Add Audio Files

Place your MP3, WAV, or M4A audio files in the `audio` directory:

```bash
# Example
cp /path/to/your/horror_music.mp3 ./audio/
cp /path/to/your/creepy_sounds.wav ./audio/
```

## Optional: YouTube Upload Setup

If you want to upload videos directly to YouTube:

### 1. Google Cloud Project Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select existing one
3. Enable the YouTube Data API v3:
   - Go to "APIs & Services" > "Library"
   - Search for "YouTube Data API v3"
   - Click "Enable"

### 2. Create OAuth Credentials

1. Go to "APIs & Services" > "Credentials"
2. Click "Create Credentials" > "OAuth 2.0 Client ID"
3. Configure OAuth consent screen if prompted
4. Choose "Desktop application" as application type
5. Download the JSON file
6. Rename it to `client_secret.json`
7. Place it in your project directory

## Verification

Test your installation:

```bash
# Navigate to your project directory
cd /path/to/your/project

# Activate virtual environment if using one
source horror_quotes_env/bin/activate

# Test the script
python3 horror_movie_quote_generator.py --quotes 1 --duration 5
```

This should:
1. Connect to OpenAI API
2. Generate 1 horror movie quote
3. Create background images
4. Process your audio (if present)
5. Generate a short test video

## Troubleshooting

### Common Issues

**"No module named 'openai'"**
```bash
pip install openai
```

**"FFmpeg not found"**
- Ensure FFmpeg is installed and in your PATH
- Test with: `ffmpeg -version`

**"OpenAI API error"**
- Check your API key in the `.env` file
- Ensure you have OpenAI credits available
- Verify the key has no extra spaces or characters

**Permission errors on Linux**
```bash
# Make script executable
chmod +x horror_movie_quote_generator.py

# Or run with python3 explicitly
python3 horror_movie_quote_generator.py
```

**Font warnings**
- The script will work with default fonts
- For better fonts on Linux: `sudo apt install fonts-dejavu-core`

### Performance Tips

**For faster processing:**
- Use an SSD for storage
- Ensure adequate RAM (4GB+ recommended)
- Close unnecessary applications during video generation

**For better quality:**
- Use high-quality audio files (192kbps+ MP3 or uncompressed WAV)
- Ensure audio files are long enough for your video duration

## Uninstallation

To remove the project:

```bash
# Deactivate virtual environment
deactivate

# Remove project directory
rm -rf /path/to/your/project

# Remove FFmpeg if no longer needed (optional)
# Ubuntu/Debian: sudo apt remove ffmpeg
# macOS: brew uninstall ffmpeg
```

## Next Steps

After installation, see the README.md file for usage instructions and examples.
