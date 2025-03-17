import os
import zipfile
import tempfile
import logging
import struct
import threading
from io import BytesIO

import telebot
import pyembroidery
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from flask import Flask

# Attempt to import the constant for color change
try:
    from pyembroidery import STITCH_COLOR_CHANGE
except ImportError:
    STITCH_COLOR_CHANGE = 0x03

from pyembroidery import read, write, EmbPattern, supported_formats

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Telegram Bot Token
BOT_TOKEN = os.getenv("BOT_TOKEN")
FORWARD_GROUP_ID = os.getenv("FORWARD_GROUP_ID")  # e.g., -1001234567890

# Initialize the bot
bot = telebot.TeleBot(BOT_TOKEN)

# Flask app for health check
app = Flask(__name__)

@app.route("/")
def health_check():
    return "Bot is running!", 200

def run_flask():
    app.run(host="0.0.0.0", port=8080)  # Required for Koyeb health check

def read_emb(file_path):
    """Custom .emb parser (adjust based on actual .emb structure)"""
    try:
        with open(file_path, "rb") as f:
            header = f.read(4)
            if header != b'EMB1':
                raise ValueError("Invalid .emb file header")
            num_stitches = struct.unpack('<I', f.read(4))[0]
            stitches = [(struct.unpack('<h', f.read(2))[0], struct.unpack('<h', f.read(2))[0]) for _ in range(num_stitches)]
            
            pattern = EmbPattern()
            for x, y in stitches:
                pattern.add_stitch_absolute(x, y)
            return pattern
    except Exception as e:
        logger.error(f"Error reading .emb file: {e}")
        return None

def export_all_formats(pattern, output_dir, base_filename):
    """Export embroidery pattern to multiple formats"""
    os.makedirs(output_dir, exist_ok=True)
    
    excluded_formats = {'col', 'edr', 'gcode', 'inf', 'pmv', 'csv', 'json', 'txt', 'fxy', 'new', 'zxy', 'tap'}
    formats = list(set(f["extension"] for f in supported_formats() if isinstance(f, dict) and "extension" in f) - excluded_formats)

    exported_files = []
    for ext in formats:
        if ext.lower() == "emb": 
            continue
        output_path = os.path.join(output_dir, f"{base_filename}.{ext}")
        try:
            write(pattern, output_path)
            exported_files.append(output_path)
        except Exception as e:
            logger.error(f"Failed to export {ext}: {e}")
    
    return exported_files

def generate_preview(pattern, output_dir, base_filename):
    """Generate a preview image of the embroidery pattern."""
    output_path = os.path.join(output_dir, f"{base_filename}_preview.png")
    colors = ['blue', 'green', 'red', 'orange', 'purple', 'brown', 'cyan']
    
    segments = []
    current_segment, color_index = [], 0
    current_color = colors[color_index]

    for stitch in pattern.stitches:
        command = stitch[2] if len(stitch) >= 3 else None
        if command == STITCH_COLOR_CHANGE:
            if current_segment:
                segments.append((current_segment, current_color))
            color_index = (color_index + 1) % len(colors)
            current_color = colors[color_index]
            current_segment = []
        else:
            current_segment.append((stitch[0], -stitch[1]))  

    if current_segment:
        segments.append((current_segment, current_color))
    
    plt.figure(figsize=(10, 10))
    for seg, col in segments:
        if seg:
            xs, ys = zip(*seg)
            plt.plot(xs, ys, color=col, linewidth=0.5)
    
    plt.axis('equal')
    plt.title("Embroidery Preview")
    plt.savefig(output_path)
    plt.close()
    
    return output_path

def process_embroidery_file(file_path, original_filename):
    """Process an embroidery file and return a list of output files"""
    base_filename = os.path.splitext(original_filename)[0]
    
    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            pattern = read(file_path)
            if pattern is None:
                raise ValueError("PyEmbroidery failed to read file.")
        except Exception:
            pattern = read_emb(file_path)

        if pattern:
            exported_files = export_all_formats(pattern, temp_dir, base_filename)
            preview_path = generate_preview(pattern, temp_dir, base_filename)
            exported_files.append(preview_path)

            zip_buffer = BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                for file in exported_files:
                    zip_file.write(file, os.path.basename(file))
            
            zip_buffer.seek(0)
            return zip_buffer, f"{base_filename}.zip"
        else:
            return None, None

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message, 
                 "Send me an embroidery file (.jef, .pes, .dst, .exp, etc.) "
                 "and I'll convert it to multiple formats with a preview image.")

@bot.message_handler(content_types=['document'])
def handle_document(message):
    try:
        processing_msg = bot.reply_to(message, "Processing your file... Please wait.")
        file_info = bot.get_file(message.document.file_id)
        original_filename = message.document.file_name
        file_extension = os.path.splitext(original_filename)[1].lower()
        
        supported_extensions = ['.jef', '.pes', '.dst', '.exp', '.emb', '.vp3', '.xxx']
        if file_extension not in supported_extensions:
            bot.edit_message_text("Unsupported file type. Try .jef, .pes, .dst, .exp, etc.", chat_id=message.chat.id, message_id=processing_msg.message_id)
            return
        
        downloaded_file = bot.download_file(file_info.file_path)
        
        with tempfile.NamedTemporaryFile(suffix=file_extension, delete=False) as temp_file:
            temp_file.write(downloaded_file)
            temp_file_path = temp_file.name
        
        try:
            zip_buffer, zip_filename = process_embroidery_file(temp_file_path, original_filename)
            
            if zip_buffer:
                bot.send_document(message.chat.id, document=zip_buffer, visible_file_name=zip_filename, caption="Here is your converted file!")
                bot.delete_message(chat_id=message.chat.id, message_id=processing_msg.message_id)
            else:
                bot.edit_message_text("Failed to process the file.", chat_id=message.chat.id, message_id=processing_msg.message_id)
        finally:
            if os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
    
    except Exception as e:
        logger.error(f"Error processing file: {e}")
        bot.reply_to(message, f"Error: {e}")

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()  # Flask health check server
    bot.infinity_polling()  # Start Telegram bot
