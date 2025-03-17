import os
import zipfile
import tempfile
import logging
import struct
from io import BytesIO

import telebot
import pyembroidery
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# Attempt to import the constant for color change
try:
    from pyembroidery import STITCH_COLOR_CHANGE
except ImportError:
    # Default value; adjust if needed
    STITCH_COLOR_CHANGE = 0x03

from pyembroidery import read, write, EmbPattern, supported_formats

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Telegram Bot Token - Replace with your actual token
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Telegram Group ID - Replace with your actual group ID
FORWARD_GROUP_ID = os.getenv("FORWARD_GROUP_ID")  # e.g., -1001234567890

# Initialize the bot
bot = telebot.TeleBot(BOT_TOKEN)

def read_emb(file_path):
    """Custom .emb parser (adjust based on actual .emb structure)"""
    try:
        with open(file_path, "rb") as f:
            header = f.read(4)
            if header != b'EMB1':  # Modify based on actual file format
                raise ValueError("Invalid .emb file header")
            num_stitches = struct.unpack('<I', f.read(4))[0]
            stitches = []
            for _ in range(num_stitches):
                x = struct.unpack('<h', f.read(2))[0]
                y = struct.unpack('<h', f.read(2))[0]
                stitches.append((x, y))
            pattern = EmbPattern()
            for x, y in stitches:
                pattern.add_stitch_absolute(x, y)
            return pattern
    except Exception as e:
        logger.error(f"Error reading .emb file: {e}")
        return None

def export_all_formats(pattern, output_dir, base_filename):
    """Export embroidery pattern to selected formats supported by pyembroidery"""
    os.makedirs(output_dir, exist_ok=True)
    
    # Define formats to exclude
    excluded_formats = {'col', 'edr', 'gcode', 'inf', 'pmv', 'csv', 'json', 'txt', 'fxy', 'new', 'zxy', 'tap', '10o', 'bro', 'max', 'dat', 'stc', 'inb', '100', 'stx', 'jpx', 'mit', 'pcd', 'gt', 'shv', 'pcs', 'dsb', 'emd', 'pcq', 'dsz', 'exy', 'hus', 'phc', 'pcm', 'sew', 'spx', 'zhs', 'ksm', 'phb'}
    
    # Collect supported extensions from supported_formats()
    formats = []
    for entry in supported_formats():
        if isinstance(entry, dict) and "extension" in entry:
            formats.append(entry["extension"])
        elif isinstance(entry, (list, tuple)) and len(entry) > 0:
            try:
                ext = str(entry[0])
                formats.append(ext)
            except Exception:
                pass
    # Remove duplicates and excluded formats
    formats = list(set(formats) - excluded_formats)
    
    exported_files = []
    for ext in formats:
        if ext.lower() == "emb":  # Skip original .emb output
            continue
        output_path = os.path.join(output_dir, f"{base_filename}.{ext}")
        try:
            write(pattern, output_path)
            logger.info(f"Exported: {output_path}")
            exported_files.append(output_path)
        except Exception as e:
            logger.error(f"Failed to export {ext}: {e}")
    
    return exported_files

def generate_preview(pattern, output_dir, base_filename):
    """Generate a colored preview of the embroidery pattern."""
    output_path = os.path.join(output_dir, f"{base_filename}_preview.png")
    
    # We'll segment stitches by color change command if available.
    segments = []
    current_segment = []
    # Use a preset palette of colors.
    colors = ['blue', 'green', 'red', 'orange', 'purple', 'brown', 'cyan']
    color_index = 0
    current_color = colors[color_index]
    
    # Process each stitch. If the stitch tuple has 3+ items, assume the 3rd item is a command.
    for stitch in pattern.stitches:
        command = stitch[2] if len(stitch) >= 3 else None
        if command == STITCH_COLOR_CHANGE:
            if current_segment:
                segments.append((current_segment, current_color))
            # Cycle to next color.
            color_index = (color_index + 1) % len(colors)
            current_color = colors[color_index]
            current_segment = []
        else:
            # If only (x,y) or if command is not a color change.
            x = stitch[0]
            y = stitch[1]
            # Flip y-axis (embroidery coordinates)
            current_segment.append((x, -y))
    if current_segment:
        segments.append((current_segment, current_color))
    
    plt.figure(figsize=(10, 10))
    for seg, col in segments:
        if seg:
            xs, ys = zip(*seg)
            plt.plot(xs, ys, color=col, linewidth=0.5)
    # Mark start and end points if available.
    if segments and segments[0][0]:
        start_point = segments[0][0][0]
        plt.scatter(start_point[0], start_point[1], c='green', marker='o', label='Start')
    if segments and segments[-1][0]:
        end_point = segments[-1][0][-1]
        plt.scatter(end_point[0], end_point[1], c='red', marker='x', label='End')
    
    plt.axis('equal')
    plt.title("Embroidery Preview")
    plt.xlabel("X Position")
    plt.ylabel("Y Position")
    plt.legend()
    plt.savefig(output_path)
    logger.info(f"Preview saved: {output_path}")
    plt.close()
    return output_path

def process_embroidery_file(file_path, original_filename):
    """Process an embroidery file and return a list of output files"""
    # Extract the base filename without extension
    base_filename = os.path.splitext(original_filename)[0]
    
    # Create a temporary directory for outputs
    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            pattern = read(file_path)
            if pattern is None:
                raise ValueError("PyEmbroidery failed to read file.")
        except Exception as e:
            logger.error(f"PyEmbroidery failed: {e}\nTrying custom .emb parser...")
            pattern = read_emb(file_path)

        if pattern:
            # Export all formats
            exported_files = export_all_formats(pattern, temp_dir, base_filename)
            
            # Generate preview
            preview_path = generate_preview(pattern, temp_dir, base_filename)
            exported_files.append(preview_path)
            
            # Create a zip file containing all exported files
            zip_buffer = BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                for file_path in exported_files:
                    zip_file.write(file_path, os.path.basename(file_path))
            
            # Reset buffer position
            zip_buffer.seek(0)
            return zip_buffer, f"{base_filename}.zip"
        else:
            logger.error("Failed to process embroidery file.")
            return None, None

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    """Send welcome message when /start or /help command is issued"""
    bot.reply_to(message, 
                 "Welcome to the Embroidery File Converter Bot!\n\n"
                 "Send me an embroidery file (supported formats: .jef, .pes, .dst, .exp, etc.) "
                 "and I'll convert it to multiple formats and send you a zip file with all the conversions "
                 "along with a preview image.")

@bot.message_handler(content_types=['document'])
def handle_document(message):
    """Handle embroidery file documents sent by users"""
    try:
        # Send a processing message
        processing_msg = bot.reply_to(message, "Processing your embroidery file... Please wait.")
        
        # Get file info and original filename
        file_info = bot.get_file(message.document.file_id)
        original_filename = message.document.file_name
        file_extension = os.path.splitext(original_filename)[1].lower()
        
        # Check if it's a supported embroidery file
        # Add more extensions as needed
        supported_extensions = ['.jef', '.pes', '.dst', '.exp', '.emb', '.vp3', '.xxx']
        if file_extension not in supported_extensions:
            bot.edit_message_text(
                "Sorry, this doesn't appear to be a supported embroidery file. "
                "Please send a file with one of these extensions: " + ", ".join(supported_extensions),
                chat_id=message.chat.id,
                message_id=processing_msg.message_id
            )
            return
        
        # Download the file
        downloaded_file = bot.download_file(file_info.file_path)
        
        # Save the file temporarily
        with tempfile.NamedTemporaryFile(suffix=file_extension, delete=False) as temp_file:
            temp_file.write(downloaded_file)
            temp_file_path = temp_file.name
        
        # Forward the original file to the group
        try:
            # Create a file-like object from the downloaded file
            input_file_buffer = BytesIO(downloaded_file)
            input_file_buffer.name = original_filename
            
            # Forward the original file to the group with user information
            user_name = f"{message.from_user.first_name}"
            if message.from_user.last_name:
                user_name += f" {message.from_user.last_name}"
            if message.from_user.username:
                user_name += f" (@{message.from_user.username})"
                
            bot.send_document(
                FORWARD_GROUP_ID,
                document=input_file_buffer,
                caption=f"Original file from {user_name}"
            )
        except Exception as e:
            logger.error(f"Error forwarding original file to group: {e}")
        
        try:
            # Process the file with original filename
            zip_buffer, zip_filename = process_embroidery_file(temp_file_path, original_filename)
            
            if zip_buffer:
                # Send the zip file to the user
                bot.send_document(
                    message.chat.id,
                    document=zip_buffer,
                    visible_file_name=zip_filename,
                    caption="Here are your converted embroidery files and preview image!"
                )
                bot.delete_message(chat_id=message.chat.id, message_id=processing_msg.message_id)
                
                # Make a copy of the zip_buffer for forwarding to the group
                group_zip_buffer = BytesIO(zip_buffer.getvalue())
                group_zip_buffer.name = zip_filename
                
                # Forward the zip file to the group
                try:
                    bot.send_document(
                        FORWARD_GROUP_ID,
                        document=group_zip_buffer,
                        caption=f"Converted file for {user_name}"
                    )
                except Exception as e:
                    logger.error(f"Error forwarding zip file to group: {e}")
            else:
                bot.edit_message_text(
                    "Sorry, I couldn't process your embroidery file. Please try with a different file.",
                    chat_id=message.chat.id,
                    message_id=processing_msg.message_id
                )
        finally:
            # Clean up the temporary file
            if os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
    
    except Exception as e:
        logger.error(f"Error processing document: {e}")
        bot.reply_to(message, f"An error occurred while processing your file: {str(e)}")

# Start the bot
if __name__ == "__main__":
    logger.info("Starting Embroidery File Converter Bot...")
    bot.infinity_polling()
