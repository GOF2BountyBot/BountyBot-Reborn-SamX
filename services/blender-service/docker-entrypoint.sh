#!/bin/bash

# Configuration variables
TARGET_DIR="/app/data/game-objects"
TEMP_FILE="/tmp/downloaded_file.7z"
GAME_OBJECTS_READY=false

# Function to check if directory exists and has files
check_directory() {
    # Look for the first .bmp or .jpg file (case‐insensitive) and quit as soon as one is found
    if find "$TARGET_DIR" -type f \( -iname '*.bmp' -o -iname '*.jpg' \) -print -quit | grep -q .; then
        echo "✓ Found at least one .bmp or .jpg under '$TARGET_DIR', assuming assets have been downloaded previously."
        return 0
    else
        echo "⚠ No .bmp or .jpg files found in '$TARGET_DIR' or its subdirectories."
        return 1
    fi
}

# Function to download and extract
download_and_extract() {
    echo "Creating directory structure..."
    if ! mkdir -p "$TARGET_DIR"; then
        echo "✗ Error: Failed to create directory structure."
        return 1
    fi
    
    echo "Downloading file using gdown..."
    if gdown "$GAME_OBJS_FILEID" -O "$TEMP_FILE" -q; then
        echo "✓ Download completed successfully."
    else
        echo "✗ Error: Failed to download file with gdown."
        return 1
    fi
    
    echo "Extracting archive to $TARGET_DIR..."
    if 7z x "$TEMP_FILE" -o"$TARGET_DIR/../" -y -aos > /dev/null; then
        echo "✓ Extraction completed successfully."
        rm -f "$TEMP_FILE"  # Clean up temporary file

        # The archive contains a "game objects" folder (with a space).
        # Rename it to the expected hyphenated "game-objects" directory.
        EXTRACTED_DIR="$TARGET_DIR/../game objects"
        if [ -d "$EXTRACTED_DIR" ] && [ ! -d "$TARGET_DIR" ]; then
            echo "Renaming 'game objects' → 'game-objects'..."
            mv "$EXTRACTED_DIR" "$TARGET_DIR"
            echo "✓ Renamed successfully."
        elif [ -d "$EXTRACTED_DIR" ] && [ -d "$TARGET_DIR" ]; then
            # Target already exists; merge contents then remove the space-named dir
            echo "Merging 'game objects' into existing 'game-objects'..."
            cp -rn "$EXTRACTED_DIR"/* "$TARGET_DIR"/ 2>/dev/null || true
            rm -rf "$EXTRACTED_DIR"
            echo "✓ Merged and cleaned up."
        fi

        return 0
    else
        echo "✗ Error: Failed to extract archive."
        rm -f "$TEMP_FILE"  # Clean up temporary file even on failure
        return 1
    fi
}

# Function to check required tools
check_dependencies() {
    local missing_tools=()
    
    if ! command -v 7z &> /dev/null; then
        missing_tools+=("7z (7zip)")
    fi
    
    if ! command -v gdown &> /dev/null; then
        missing_tools+=("gdown")
    fi
    
    if [[ ${#missing_tools[@]} -gt 0 ]]; then
        echo "✗ Error: Missing required tools: ${missing_tools[*]}"
        echo "Install missing tools and try again."
        exit 1
    fi
}

# Main execution
echo "=== Game Objects Setup and Application Launcher ==="
echo

# Check dependencies first
check_dependencies

# Check directory status
echo "Checking game objects directory status..."

if check_directory; then
    # Directory already exists and appears to have all needed files
    GAME_OBJECTS_READY=true
    echo "✓ Game objects data is already available."
else
    # Directory is missing or empty, try to download and extract
    echo "Directory is missing or incomplete. Attempting download and extraction..."
    
    if download_and_extract; then
        # Verify the extraction worked by checking directory again
        if check_directory; then
            GAME_OBJECTS_READY=true
            echo "✓ Game objects data successfully downloaded and extracted."
        else
            echo "✗ Error: Extraction completed but data still missing."
        fi
    else
        echo "✗ Error: Download and extraction process failed."
    fi
fi

# GPU Detection and CUDA Kernel Pre-compilation
# optiX would be preferred, but the libs aren't available for WSL yet,
# and hacking them in would be a pain.
echo
echo "=== Blender CUDA Setup ==="
if command -v nvidia-smi >/dev/null 2>&1 && [ "$(nvidia-smi --list-gpus 2>/dev/null | wc -l)" -gt 0 ]; then
    echo "🚀 GPU detected - running test render to pre-compile CUDA kernels (1-5 seconds, one-time only)..."
    
    cat > /tmp/warmup.py << 'EOF'
import bpy
bpy.context.scene.render.engine = 'CYCLES'
bpy.context.preferences.addons['cycles'].preferences.compute_device_type = 'CUDA'
bpy.context.preferences.addons['cycles'].preferences.get_devices()
for device in bpy.context.preferences.addons['cycles'].preferences.devices:
    device.use = device.type == 'CUDA'
if any(d.use for d in bpy.context.preferences.addons['cycles'].preferences.devices if d.type == 'CUDA'):
    bpy.context.scene.cycles.device = 'GPU'
    bpy.context.scene.cycles.samples = 1
    bpy.context.scene.render.resolution_x = 64
    bpy.context.scene.render.resolution_y = 64
    bpy.ops.render.render()
EOF
    
    # Comment below to disable blender warmup at container startup

    if [[ "$DO_WARMUP" == true ]]; then
        WARMUP_LOG="/tmp/blender_warmup.log"
        blender -b -P /tmp/warmup.py --background >"$WARMUP_LOG" 2>&1
        WARMUP_EXIT=$?
        rm -f /tmp/warmup.py
        if [[ $WARMUP_EXIT -eq 0 ]]; then
            echo "✅ CUDA kernels compiled - GPU renders will start instantly!"
        else
            echo "⚠️  WARNING: CUDA warmup failed (exit code $WARMUP_EXIT) - first render may be slower."
            echo "   Log tail (last 20 lines of $WARMUP_LOG):"
            tail -n 20 "$WARMUP_LOG" | sed 's/^/   /'
        fi
    else
        echo "✅ GPU Detected, but warmup option was disabled.  First render will take extra time to compile CUDA kernels."
    fi
else
    echo "⚡ No GPU detected - using CPU rendering"
fi

echo
echo "=== Final Status Check ==="

# Launch Python app or show warning based on game objects availability
if [[ "$GAME_OBJECTS_READY" == true ]]; then
    echo "✓ Game objects data is ready. Proceeding with application launch..."
    echo
    # Force-reupdate permissions on the bot folder...
    sudo chown -R botuser /app
    sudo chmod -R 1777 /app
    source /opt/venv/bin/activate
    /opt/venv/bin/python /app/src/main.py "$CONFIG_FILE"
else
    echo "⚠️  WARNING: Game objects data is not available!"
    echo "   The Python application will NOT be launched."
    echo "   Please check your internet connection, download URL, or file permissions."
    echo "   Manual intervention may be required."
    exit 1
fi