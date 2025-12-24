"""
Post generation endpoints.
"""
import random
import asyncio
import uuid
from datetime import datetime
from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from config import DEFAULT_PHONE_NUMBER
from models import GeneratePostResponse
from database import UserRepository
from services import (
    parse_csv_for_today,
    generate_structured_output,
    generate_image,
    overlay_images,
    image_to_base64,
    send_to_whatsapp,
)

router = APIRouter(tags=["Posts"])

# In-memory job tracker 
distribution_jobs = {}


@router.post("/generate-post", response_model=GeneratePostResponse)
async def generate_post(
    holiday: str = Query(None, description="Holiday name (defaults to today's CSV entry)"),
    phone: str = Query(DEFAULT_PHONE_NUMBER, description="Receiver phone number"),
    mail: str = Query("ANDROCODERS21@GMAIL.COM", description="Email for footer"),
    website: str = Query("ANDROCODERS.IN", description="Website for footer"),
):
    """
    Generate and send a custom holiday post.
    Useful for testing specific holidays or branding.
    """
    # Step 1: Resolve Holiday
    if not holiday:
        holiday = parse_csv_for_today()
        if not holiday:
            raise HTTPException(
                status_code=404,
                detail="No holiday found for today and no holiday parameter provided"
            )

    # Step 2: Generate structured output (prompt and caption)
    structured_output = generate_structured_output(holiday)
    image_prompt = structured_output.get("prompt", "")
    caption = structured_output.get("caption", "")

    if not image_prompt:
        raise HTTPException(status_code=500, detail="Failed to generate image prompt")

    # Step 3: Generate and Customize Image
    generated_image = generate_image(image_prompt)

    footer = f"+91 {phone}   |   {mail.upper()}   |   {website.upper()}"
    final_image = overlay_images(generated_image, footer_text=footer)

    # Step 4: Convert to base64
    image_base64 = image_to_base64(final_image)

    # Step 5: Send to WhatsApp
    try:
        await send_to_whatsapp(image_base64, caption, phone=phone)
        return GeneratePostResponse(
            success=True,
            holiday=holiday,
            caption=caption,
            message=f"Post generated and sent to {phone} successfully!",
        )
    except Exception as e:
        return GeneratePostResponse(
            success=False,
            holiday=holiday,
            caption=caption,
            message=f"Post generated but failed to send: {str(e)}",
        )


@router.post("/distribute-holiday-post")
async def distribute_holiday_post(background_tasks: BackgroundTasks):
    """
    Generate a holiday post once and send customized versions to all users
    with randomized staggered delays to avoid rate-limiting/bans.
    
    Returns immediately with a job_id. Use /distribution-status/{job_id} to check progress.
    """
    # 1. Get Today's Holiday
    holiday = parse_csv_for_today()
    if not holiday:
        return {"status": "error", "message": "No holiday found for today"}

    # 2. Get All Users
    users = await UserRepository.get_all_raw()

    if not users:
        return {"status": "error", "message": "No users found in database"}

    # 3. Generate Base Image (Once)
    structured_output = generate_structured_output(holiday)
    image_prompt = structured_output.get("prompt", "")
    caption = structured_output.get("caption", "")

    if not image_prompt:
        raise HTTPException(status_code=500, detail="Failed to generate image prompt")

    generated_base_image = generate_image(image_prompt)

    # 4. Create a job ID and start background task
    job_id = str(uuid.uuid4())
    distribution_jobs[job_id] = {
        "status": "running",
        "holiday": holiday,
        "total_users": len(users),
        "processed": 0,
        "successful": 0,
        "failed": 0,
        "started_at": datetime.now().isoformat(),
        "results": []
    }

    # Start background task
    background_tasks.add_task(
        _process_distribution,
        job_id,
        users,
        generated_base_image,
        caption
    )

    return {
        "status": "started",
        "job_id": job_id,
        "holiday": holiday,
        "total_users": len(users),
        "message": f"Distribution started for {len(users)} users. Check status at /distribution-status/{job_id}"
    }


async def _process_distribution(job_id: str, users: list, base_image, caption: str):
    """Background task to process the distribution with staggered delays."""
    job = distribution_jobs[job_id]
    
    for index, user in enumerate(users):
        try:
            # Custom footer: "Phone | Mail | Website"
            footer = f"{user.get('phone', '')}   |   {user.get('mail', '').upper()}   |   {user.get('website', '').upper()}"

            # Overlay specific logo and footer
            custom_image = overlay_images(
                base_image,
                logo_data=user.get("logo"),
                footer_text=footer
            )

            # Send
            image_b64 = image_to_base64(custom_image)

            # Wait for a random time before sending (except first user)
            if index > 0:
                delay_seconds = random.randint(30, 300)
                print(f"[Job {job_id}] Waiting {delay_seconds}s before sending to {user.get('phone')}...")
                await asyncio.sleep(delay_seconds)

            api_res = await send_to_whatsapp(image_b64, caption, phone=user.get("phone"))

            job["results"].append({
                "user_id": str(user["_id"]),
                "phone": user.get("phone"),
                "success": True,
                "api_response": api_res
            })
            job["successful"] += 1

        except Exception as e:
            job["results"].append({
                "user_id": str(user["_id"]),
                "phone": user.get("phone"),
                "success": False,
                "error": str(e)
            })
            job["failed"] += 1
        
        job["processed"] += 1
    
    job["status"] = "completed"
    job["completed_at"] = datetime.now().isoformat()
    print(f"[Job {job_id}] Distribution completed: {job['successful']} successful, {job['failed']} failed")


@router.get("/distribution-status/{job_id}")
async def get_distribution_status(job_id: str):
    """
    Check the status of a distribution job.
    """
    if job_id not in distribution_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    return distribution_jobs[job_id]

