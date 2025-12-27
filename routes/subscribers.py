"""
Subscriber management endpoints.
"""
import io
import base64
import random
import asyncio
import uuid
from datetime import datetime
from fastapi import APIRouter, HTTPException, File, UploadFile, Form, BackgroundTasks
from PIL import Image
from config import MONGO_URI
from database import SubscriberRepository, HolidayRepository
from models.schemas import SendFestivalRequest
from services import (
    get_holiday_for_today,
    get_holiday_with_description_for_today,
    generate_structured_output,
    generate_image,
    overlay_subscriber_image,
    image_to_base64,
    send_to_whatsapp,
)

router = APIRouter(prefix="/subscriber", tags=["Subscribers"])

# In-memory job tracker for subscriber distributions
subscriber_distribution_jobs = {}


@router.post("")
async def create_subscriber(
    overlay: UploadFile = File(...),
    phone: str = Form(...),
    name: str = Form(""),
):
    """Create a new subscriber with overlay image, phone number, and name."""
    if not MONGO_URI:
        raise HTTPException(status_code=500, detail="MONGO_URI not configured")

    try:
        # Read and validate the overlay image
        overlay_content = await overlay.read()
        try:
            # Validate it's a valid image
            img = Image.open(io.BytesIO(overlay_content))
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            # Save back to bytes
            output = io.BytesIO()
            img.save(output, format="PNG")
            overlay_bytes = output.getvalue()
        except Exception as img_err:
            raise HTTPException(
                status_code=400, detail=f"Invalid image file: {str(img_err)}"
            )

        # Convert to base64 for storage
        overlay_base64 = base64.b64encode(overlay_bytes).decode("utf-8")

        subscriber_id = await SubscriberRepository.create(
            phone=phone,
            overlay_base64=overlay_base64,
            name=name,
        )

        return {
            "status": "success",
            "message": "Subscriber created successfully",
            "id": subscriber_id,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("")
async def list_subscribers():
    """List all subscribers."""
    return await SubscriberRepository.get_all()


@router.get("/{subscriber_id}")
async def get_subscriber(subscriber_id: str):
    """Get a specific subscriber's details."""
    return await SubscriberRepository.get_by_id(subscriber_id)


@router.put("/{subscriber_id}")
async def update_subscriber(
    subscriber_id: str,
    phone: str = Form(None),
    name: str = Form(None),
    overlay: UploadFile = File(None),
):
    """Update subscriber details."""
    update_data = {}
    if phone:
        update_data["phone"] = phone
    if name:
        update_data["name"] = name

    if overlay:
        try:
            overlay_content = await overlay.read()
            img = Image.open(io.BytesIO(overlay_content))
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            output = io.BytesIO()
            img.save(output, format="PNG")
            overlay_bytes = output.getvalue()
            update_data["overlay"] = base64.b64encode(overlay_bytes).decode("utf-8")
        except Exception as img_err:
            raise HTTPException(
                status_code=400, detail=f"Invalid image file: {str(img_err)}"
            )

    return await SubscriberRepository.update(subscriber_id, update_data)


@router.delete("/{subscriber_id}")
async def delete_subscriber(subscriber_id: str):
    """Delete a subscriber."""
    return await SubscriberRepository.delete(subscriber_id)


@router.post("/distribute")
async def distribute_to_subscribers(background_tasks: BackgroundTasks):
    """
    Generate a holiday post and send it to all subscribers with their custom overlays.

    Returns immediately with a job_id. Use /subscriber/distribution-status/{job_id} to check progress.
    """
    # 1. Get Today's Holiday with description
    holiday_data = await get_holiday_with_description_for_today()
    if not holiday_data:
        return {"status": "error", "message": "No holiday found for today"}

    holiday = holiday_data.get("prompt")
    holiday_description = holiday_data.get("description")

    # 2. Get All Subscribers
    subscribers = await SubscriberRepository.get_all_raw()

    if not subscribers:
        return {"status": "error", "message": "No subscribers found in database"}

    # 3. Create a job ID and start background task immediately
    job_id = str(uuid.uuid4())
    subscriber_distribution_jobs[job_id] = {
        "status": "running",
        "holiday": holiday,
        "total_subscribers": len(subscribers),
        "processed": 0,
        "successful": 0,
        "failed": 0,
        "started_at": datetime.now().isoformat(),
        "results": []
    }

    # Start background task (image generation happens inside)
    background_tasks.add_task(
        _process_subscriber_distribution,
        job_id,
        subscribers,
        holiday,
        holiday_description
    )

    return {
        "status": "started",
        "job_id": job_id,
        "holiday": holiday,
        "total_subscribers": len(subscribers),
        "message": f"Distribution started for {len(subscribers)} subscribers. Check status at /subscriber/distribution-status/{job_id}"
    }


async def _process_subscriber_distribution(job_id: str, subscribers: list, holiday: str, holiday_description: str = None):
    """Background task to process the subscriber distribution with staggered delays."""
    job = subscriber_distribution_jobs[job_id]

    print(f"\n{'='*60}")
    print(f"[Job {job_id}] STARTING DISTRIBUTION")
    print(f"[Job {job_id}] Holiday: {holiday}")
    print(f"[Job {job_id}] Description: {holiday_description}")
    print(f"[Job {job_id}] Total subscribers: {len(subscribers)}")
    print(f"{'='*60}\n")

    try:
        # Generate Base Image (Once) - now happens in background
        print(f"[Job {job_id}] Generating structured output...")
        structured_output = generate_structured_output(holiday, holiday_description)
        image_prompt = structured_output.get("prompt", "")
        caption = structured_output.get("caption", "")

        print(f"[Job {job_id}] Caption: {caption}")
        print(f"[Job {job_id}] Prompt: {image_prompt[:20]}...")

        if not image_prompt:
            job["status"] = "failed"
            job["error"] = "Failed to generate image prompt"
            print(f"[Job {job_id}] ERROR: Failed to generate image prompt")
            return

        print(f"[Job {job_id}] Generating base image...")
        base_image = generate_image(image_prompt)
        print(f"[Job {job_id}] Base image generated successfully: {base_image.size}")
    except Exception as e:
        job["status"] = "failed"
        job["error"] = f"Image generation failed: {str(e)}"
        print(f"[Job {job_id}] ERROR: Image generation failed: {str(e)}")
        return

    for index, subscriber in enumerate(subscribers):
        sub_name = subscriber.get("name", "Unknown")
        sub_phone = subscriber.get("phone", "No phone")
        sub_id = str(subscriber["_id"])

        print(f"\n[Job {job_id}] --- Subscriber {index + 1}/{len(subscribers)} ---")
        print(f"[Job {job_id}] Name: {sub_name}")
        print(f"[Job {job_id}] Phone: {sub_phone}")
        print(f"[Job {job_id}] ID: {sub_id}")

        try:
            # Decode overlay from base64
            overlay_base64 = subscriber.get("overlay", "")
            overlay_bytes = base64.b64decode(overlay_base64)
            print(f"[Job {job_id}] Overlay decoded: {len(overlay_bytes)} bytes")

            # Composite the overlay on the generated image
            custom_image = overlay_subscriber_image(base_image, overlay_bytes)
            print(f"[Job {job_id}] Image composited: {custom_image.size}")

            # Convert to base64 for sending
            image_b64 = image_to_base64(custom_image)

            # Wait for a random time before sending (except first subscriber)
            if index > 0:
                delay_seconds = random.randint(240, 480)  # 4-8 minutes
                delay_mins = delay_seconds / 60
                print(f"[Job {job_id}] ⏳ Waiting {delay_mins:.1f} mins ({delay_seconds}s) before sending to {sub_name} ({sub_phone})...")
                await asyncio.sleep(delay_seconds)

            api_res = await send_to_whatsapp(image_b64, caption, phone=sub_phone)
            print(f"[Job {job_id}] WhatsApp API Response: {api_res}")

            job["results"].append({
                "subscriber_id": sub_id,
                "name": sub_name,
                "phone": sub_phone,
                "success": True,
                "api_response": api_res
            })
            job["successful"] += 1
            print(f"[Job {job_id}] SUCCESS: Message sent to {sub_name} ({sub_phone})")

        except Exception as e:
            print(f"[Job {job_id}] ERROR for {sub_name} ({sub_phone}): {str(e)}")
            job["results"].append({
                "subscriber_id": sub_id,
                "name": sub_name,
                "phone": sub_phone,
                "success": False,
                "error": str(e)
            })
            job["failed"] += 1

        job["processed"] += 1

    job["status"] = "completed"
    job["completed_at"] = datetime.now().isoformat()
    print(f"\n{'='*60}")
    print(f"[Job {job_id}] DISTRIBUTION COMPLETED")
    print(f"[Job {job_id}] Successful: {job['successful']}")
    print(f"[Job {job_id}] Failed: {job['failed']}")
    print(f"{'='*60}\n")


@router.post("/distribute/{subscriber_id}")
async def distribute_to_single_subscriber(subscriber_id: str, background_tasks: BackgroundTasks):
    """
    Generate a holiday post and send it to a specific subscriber by ID.

    Returns immediately with a job_id. Use /subscriber/distribution-status/{job_id} to check progress.
    """
    # 1. Get Today's Holiday with description
    holiday_data = await get_holiday_with_description_for_today()
    if not holiday_data:
        return {"status": "error", "message": "No holiday found for today"}

    holiday = holiday_data.get("prompt")
    holiday_description = holiday_data.get("description")

    # 2. Get the specific subscriber
    subscriber = await SubscriberRepository.get_by_id(subscriber_id)
    if not subscriber:
        raise HTTPException(status_code=404, detail="Subscriber not found")

    # Convert to raw format for processing (need to get with overlay)
    from bson import ObjectId
    from database import get_subscribers_collection
    raw_subscriber = await get_subscribers_collection().find_one({"_id": ObjectId(subscriber_id)})

    if not raw_subscriber:
        raise HTTPException(status_code=404, detail="Subscriber not found")

    # 3. Create a job ID and start background task immediately
    job_id = str(uuid.uuid4())
    subscriber_distribution_jobs[job_id] = {
        "status": "running",
        "holiday": holiday,
        "total_subscribers": 1,
        "processed": 0,
        "successful": 0,
        "failed": 0,
        "started_at": datetime.now().isoformat(),
        "results": []
    }

    # Start background task (image generation happens inside)
    background_tasks.add_task(
        _process_subscriber_distribution,
        job_id,
        [raw_subscriber],
        holiday,
        holiday_description
    )

    return {
        "status": "started",
        "job_id": job_id,
        "holiday": holiday,
        "subscriber_id": subscriber_id,
        "message": f"Distribution started for subscriber {subscriber_id}. Check status at /subscriber/distribution-status/{job_id}"
    }


@router.get("/distribution-status/{job_id}")
async def get_subscriber_distribution_status(job_id: str):
    """
    Check the status of a subscriber distribution job.
    """
    if job_id not in subscriber_distribution_jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    return subscriber_distribution_jobs[job_id]


@router.post("/send-festival")
async def send_festival_to_subscriber(request: SendFestivalRequest):
    """
    Send a specific festival post to a specific subscriber.
    """
    # 1. Validate Subscriber
    subscriber = await SubscriberRepository.get_by_id(request.subscriber_id)
    if not subscriber:
        raise HTTPException(status_code=404, detail="Subscriber not found")

    # 2. Validate Festival (Holiday)
    holiday_data = await HolidayRepository.get_by_id(request.festival_id)
    if not holiday_data:
        raise HTTPException(status_code=404, detail="Festival not found")

    holiday_name = holiday_data.get("prompt")
    holiday_description = holiday_data.get("description")

    # 3. Get Raw Subscriber (for overlay)
    from bson import ObjectId
    from database import get_subscribers_collection
    raw_subscriber = await get_subscribers_collection().find_one({"_id": ObjectId(request.subscriber_id)})

    if not raw_subscriber:
        raise HTTPException(status_code=404, detail="Subscriber raw data not found")

    try:
        # 4. Generate Content
        print(f"Generating content for {holiday_name}...")
        structured_output = generate_structured_output(holiday_name, holiday_description)
        image_prompt = structured_output.get("prompt", "")
        caption = structured_output.get("caption", "")

        if not image_prompt:
            raise HTTPException(status_code=500, detail="Failed to generate image prompt")

        # 5. Generate Image
        print(f"Generating image with prompt: {image_prompt[:50]}...")
        base_image = generate_image(image_prompt)

        # 6. Apply Overlay
        overlay_base64 = raw_subscriber.get("overlay", "")
        if overlay_base64:
            overlay_bytes = base64.b64decode(overlay_base64)
            final_image = overlay_subscriber_image(base_image, overlay_bytes)
        else:
            final_image = base_image

        # 7. Send via WhatsApp
        image_b64 = image_to_base64(final_image)
        phone = subscriber.get("phone")

        print(f"Sending to {phone}...")
        api_res = await send_to_whatsapp(image_b64, caption, phone=phone)

        return {
            "status": "success",
            "message": "Festival post sent successfully",
            "subscriber": subscriber.get("name"),
            "festival": holiday_name,
            "api_response": api_res
        }

    except Exception as e:
        print(f"Error sending festival post: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to send post: {str(e)}")
