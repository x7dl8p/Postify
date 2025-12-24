from fastapi import FastAPI, HTTPException, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from google import genai
from google.genai import types
from PIL import Image, ImageDraw, ImageFont
import uvicorn
import csv
import os
import io
import base64
import httpx
from datetime import datetime
from dotenv import load_dotenv
from pydantic import BaseModel

# Load environment variables
load_dotenv()

# ==================== CONFIGURATION ====================
# API Keys
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MONGO_URI = os.getenv("MONGO_URI")

# File Paths
CSV_FILE_PATH = "holidays.csv"
OVERLAY_IMAGE_PATH = "overlay.png"
LOGO_IMAGE_PATH = "logo.png"

# API Endpoints
SEND_MEDIA_URL = "https://fast.meteor-fitness.com/send-media?type=base64"
PHONE_NUMBER = "9450214263"

# Gemini Models
GEMINI_TEXT_MODEL = "gemini-flash-latest"
GEMINI_IMAGE_MODEL = "gemini-3-pro-image-preview"

# Image Settings
IMAGE_SIZE = 1024

# Footer Settings
FOOTER_TEXT = "Tel: +91 8299396255  |  Email: ANDROCODERS21@GMAIL.COM  |  ANDROCODERS.IN"
FOOTER_ELEVATION = 50  # pixels from bottom
FOOTER_FONT_SIZE = 24
FOOTER_TEXT_COLOR = (255, 255, 255)  # White text

# Prompt Templates - Following Gemini best practices: describe the scene narratively
STRUCTURED_OUTPUT_PROMPT = """
You are a creative social media content designer. Based on the holiday "{holiday}", generate:

1. A detailed, narrative image generation prompt (not a keyword list, but a descriptive paragraph) for creating a 1024x1024 social media post image. The prompt should describe:
   - A modern, premium design with elegant calligraphy-style greeting text for {holiday} positioned on the LEFT side of the composition
   - A beautifully rendered, high-detail symbolic illustration representing {holiday} on the RIGHT side, with soft glowing effects and a photorealistic premium aesthetic
   - The overall scene should have a sleek, professional look with modern vibrant colors and celebratory atmosphere
   - Use photography-like quality with soft, ambient lighting that creates a warm, inviting mood
   -Edge to Edge design no borders

2. A catchy, engaging caption for posting on social media (include relevant emojis)

Respond in valid JSON format with exactly these keys:
{{
    "prompt": "your detailed narrative image prompt here",
    "caption": "your social media caption here"
}}
"""

# ==================== END CONFIGURATION ====================

app = FastAPI(
    title="Postify", description="Automated Holiday Social Media Post Generator"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

client = genai.Client(api_key=GEMINI_API_KEY)

# MongoDB Connection
mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client.get_database("postify")
collection = db.get_collection("subscribers")


class GeneratePostResponse(BaseModel):
    success: bool
    holiday: str
    caption: str
    message: str


def parse_csv_for_today() -> str | None:
    """Parse the CSV file and return today's holiday if found."""
    today = datetime.now().strftime("%d-%m-%Y")

    try:
        with open(CSV_FILE_PATH, mode="r", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            for row in reader:
                if row["Date"].strip() == today:
                    return row["Prompt"].strip()
    except FileNotFoundError:
        raise HTTPException(
            status_code=500, detail=f"CSV file not found: {CSV_FILE_PATH}"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading CSV: {str(e)}")

    return None


def generate_structured_output(holiday: str) -> dict:
    """Generate structured output with prompt and caption using Gemini Flash."""
    prompt = STRUCTURED_OUTPUT_PROMPT.format(holiday=holiday)

    response = client.models.generate_content(
        model=GEMINI_TEXT_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )

    # Parse the JSON response
    import json

    try:
        result = json.loads(response.text)
        return result
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=500, detail="Failed to parse Gemini response as JSON"
        )


def generate_image(prompt: str) -> Image.Image:
    """Generate an image using Gemini Nano Banana Pro."""

    full_prompt = f"""
Create a stunning 1024x1024 social media post image with the following scene:

{prompt}

The composition should feel premium and professionally designed, with careful attention to lighting, color harmony, and visual balance. The overall mood should be celebratory and inviting.
"""

    response = client.models.generate_content(
        model=GEMINI_IMAGE_MODEL,
        contents=[full_prompt],
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE", "TEXT"],
            image_config=types.ImageConfig(
                aspect_ratio="1:1",
                image_size="1K",
            )
        )
    )

    # Extract the generated image from response
    for part in response.candidates[0].content.parts:
        if part.inline_data is not None:
            image_data = part.inline_data.data
            return Image.open(io.BytesIO(image_data))

    raise HTTPException(status_code=500, detail="No image generated by Gemini")


def overlay_images(generated_image: Image.Image) -> Image.Image:
    """Overlay the overlay.png and logo.png on top of the generated image."""
    # Ensure the generated image is in RGBA mode
    if generated_image.mode != "RGBA":
        generated_image = generated_image.convert("RGBA")

    # Resize generated image to 1024x1024 if needed
    if generated_image.size != (IMAGE_SIZE, IMAGE_SIZE):
        generated_image = generated_image.resize(
            (IMAGE_SIZE, IMAGE_SIZE), Image.Resampling.LANCZOS
        )

    # Create a copy to work with
    final_image = generated_image.copy()

    # Layer 2: Overlay the overlay.png
    overlay = Image.open(OVERLAY_IMAGE_PATH).convert("RGBA")
    if overlay.size != (IMAGE_SIZE, IMAGE_SIZE):
        overlay = overlay.resize((IMAGE_SIZE, IMAGE_SIZE), Image.Resampling.LANCZOS)
    final_image = Image.alpha_composite(final_image, overlay)

    # Layer 3: Paste the logo on top-left with padding
    logo = Image.open(LOGO_IMAGE_PATH).convert("RGBA")
    # Resize logo to 200x200
    logo = logo.resize((120, 120), Image.Resampling.LANCZOS)
    # Paste logo at top-left with 30px padding
    final_image.paste(logo, (20, 20), logo)

    # Layer 4: Add footer text
    draw = ImageDraw.Draw(final_image)
    try:
        # Use Segoe UI on Windows for better compatibility
        font = ImageFont.truetype("segoeui.ttf", FOOTER_FONT_SIZE)
    except (IOError, OSError):
        try:
            font = ImageFont.truetype("arial.ttf", FOOTER_FONT_SIZE)
        except (IOError, OSError):
            font = ImageFont.load_default()
    
    # Calculate text position (centered horizontally, 40px from bottom)
    text_bbox = draw.textbbox((0, 0), FOOTER_TEXT, font=font)
    text_width = text_bbox[2] - text_bbox[0]
    text_height = text_bbox[3] - text_bbox[1]
    x = (IMAGE_SIZE - text_width) // 2
    y = IMAGE_SIZE - FOOTER_ELEVATION - text_height
    
    # Draw the footer text
    draw.text((x, y), FOOTER_TEXT, font=font, fill=FOOTER_TEXT_COLOR)

    return final_image


def image_to_base64(image: Image.Image) -> str:
    """Convert PIL Image to base64 string."""
    # Convert to RGB if needed (for JPEG compatibility)
    if image.mode == "RGBA":
        # Create a white background
        background = Image.new("RGB", image.size, (255, 255, 255))
        background.paste(image, mask=image.split()[3])
        image = background

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


async def send_to_whatsapp(image_base64: str, caption: str) -> dict:
    """Send the final image to WhatsApp via API."""
    payload = {"phone": PHONE_NUMBER, "message": image_base64, "caption": caption}

    async with httpx.AsyncClient() as http_client:
        response = await http_client.post(SEND_MEDIA_URL, json=payload, timeout=60.0)
        return response.json()


@app.get("/")
def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "message": "Postify API is running"}


@app.get("/today-holiday")
def get_today_holiday():
    """Get today's holiday from the CSV."""
    holiday = parse_csv_for_today()
    if holiday:
        return {"date": datetime.now().strftime("%d-%m-%Y"), "holiday": holiday}
    return {
        "date": datetime.now().strftime("%d-%m-%Y"),
        "holiday": None,
        "message": "No holiday found for today",
    }


@app.post("/generate-post", response_model=GeneratePostResponse)
async def generate_post():
    """Generate and send a holiday post."""
    # Step 1: Parse CSV for today's holiday
    holiday = parse_csv_for_today()
    if not holiday:
        raise HTTPException(status_code=404, detail="No holiday found for today's date")

    # Step 2: Generate structured output (prompt and caption)
    structured_output = generate_structured_output(holiday)
    image_prompt = structured_output.get("prompt", "")
    caption = structured_output.get("caption", "")

    if not image_prompt:
        raise HTTPException(status_code=500, detail="Failed to generate image prompt")

    generated_image = generate_image(image_prompt)

    final_image = overlay_images(generated_image)

    # Convert to base64
    image_base64 = image_to_base64(final_image)

    # Step 6: Send to WhatsApp
    try:
        api_response = await send_to_whatsapp(image_base64, caption)
        return GeneratePostResponse(
            success=True,
            holiday=holiday,
            caption=caption,
            message=f"Post generated and sent successfully! API Response: {api_response}",
        )
    except Exception as e:
        return GeneratePostResponse(
            success=False,
            holiday=holiday,
            caption=caption,
            message=f"Post generated but failed to send: {str(e)}",
        )


@app.post("/add-subscriber")
async def add_subscriber(
    logo: UploadFile = File(...),
    phone: str = Form(...),
    mail: str = Form(...),
    website: str = Form(...)
):
    """
    Add a new subscriber with logo and contact details.
    """
    if not MONGO_URI:
        raise HTTPException(status_code=500, detail="MONGO_URI not configured")

    try:
        # Read the file content
        logo_content = await logo.read()
        
        subscriber_data = {
            "phone": phone,
            "mail": mail,
            "website": website,
            "logo": logo_content,  # Storing binary data directly
            "logo_filename": logo.filename,
            "created_at": datetime.now()
        }
        
        # Insert into MongoDB
        result = await collection.insert_one(subscriber_data)
        
        return {
            "status": "success",
            "message": "Subscriber added successfully",
            "id": str(result.inserted_id)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)