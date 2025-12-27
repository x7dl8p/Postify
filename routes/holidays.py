"""
Holiday API Routes - CRUD operations for holidays.
"""
from fastapi import APIRouter, HTTPException, status
from typing import List
from models.schemas import HolidayCreate, HolidayUpdate, HolidayResponse, GeneratePromptResponse
from database import HolidayRepository
from services import generate_structured_output

router = APIRouter(prefix="/holidays", tags=["Holidays"])


@router.post(
    "/",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new holiday",
    description="Add a new holiday to the database with date, prompt, and optional description."
)
async def create_holiday(holiday: HolidayCreate):
    """Create a new holiday entry."""
    try:
        holiday_id = await HolidayRepository.create(
            date=holiday.date,
            prompt=holiday.prompt,
            description=holiday.description
        )
        return {
            "status": "success",
            "message": "Holiday created successfully",
            "id": holiday_id
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create holiday: {str(e)}"
        )


@router.get(
    "/",
    response_model=List[HolidayResponse],
    summary="Get all holidays",
    description="Retrieve all holidays from the database, sorted by date."
)
async def get_all_holidays():
    """Get all holidays."""
    try:
        holidays = await HolidayRepository.get_all()
        return holidays
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch holidays: {str(e)}"
        )


@router.get(
    "/{holiday_id}",
    response_model=HolidayResponse,
    summary="Get holiday by ID",
    description="Retrieve a specific holiday by its MongoDB ObjectId."
)
async def get_holiday_by_id(holiday_id: str):
    """Get a holiday by ID."""
    return await HolidayRepository.get_by_id(holiday_id)


@router.get(
    "/date/{date}",
    response_model=HolidayResponse,
    summary="Get holiday by date",
    description="Retrieve a holiday by its date (DD-MM-YYYY format)."
)
async def get_holiday_by_date(date: str):
    """Get a holiday by date (DD-MM-YYYY format)."""
    holiday = await HolidayRepository.get_by_date(date)
    if not holiday:
        raise HTTPException(
            status_code=404,
            detail=f"No holiday found for date: {date}"
        )
    return holiday


@router.put(
    "/{holiday_id}",
    response_model=dict,
    summary="Update a holiday",
    description="Update an existing holiday's date, prompt, or description."
)
async def update_holiday(holiday_id: str, holiday: HolidayUpdate):
    """Update a holiday by ID."""
    # Build update dict with only provided fields
    update_data = {}
    if holiday.date is not None:
        update_data["date"] = holiday.date
    if holiday.prompt is not None:
        update_data["prompt"] = holiday.prompt
    if holiday.description is not None:
        update_data["description"] = holiday.description

    if not update_data:
        raise HTTPException(
            status_code=400,
            detail="No fields provided for update"
        )

    return await HolidayRepository.update(holiday_id, update_data)


@router.delete(
    "/{holiday_id}",
    response_model=dict,
    summary="Delete a holiday",
    description="Delete a holiday by its ID."
)
async def delete_holiday(holiday_id: str):
    """Delete a holiday by ID."""
    return await HolidayRepository.delete(holiday_id)


@router.get(
    "/{holiday_id}/preview-prompt",
    response_model=GeneratePromptResponse,
    summary="Preview image generation prompt",
    description="Generate and preview the AI prompt that will be sent to the image generation model for a specific festival."
)
async def preview_image_prompt(holiday_id: str):
    """
    Preview the image generation prompt for a festival.

    This endpoint shows you exactly what will be sent to the image generation AI:
    - The festival name and description
    - The combined context sent to the text AI
    - The generated image prompt
    - The generated caption
    """
    # 1. Fetch Holiday
    holiday_data = await HolidayRepository.get_by_id(holiday_id)
    if not holiday_data:
        raise HTTPException(status_code=404, detail="Festival not found")

    festival_name = holiday_data.get("prompt")
    festival_description = holiday_data.get("description")

    # 2. Build AI input context (same as in generate_structured_output)
    if festival_description:
        ai_input_context = f"{festival_name}. Context: {festival_description}"
    else:
        ai_input_context = festival_name

    try:
        # 3. Generate structured output (prompt + caption)
        print(f"\n[Preview] Generating prompt for: {festival_name}")
        if festival_description:
            print(f"[Preview] With description: {festival_description}")

        structured_output = generate_structured_output(festival_name, festival_description)
        image_prompt = structured_output.get("prompt", "")
        caption = structured_output.get("caption", "")

        print(f"[Preview] Generated image prompt length: {len(image_prompt)} characters")
        print(f"[Preview] Generated caption: {caption}")

        return GeneratePromptResponse(
            festival_name=festival_name,
            festival_description=festival_description,
            ai_input_context=ai_input_context,
            generated_image_prompt=image_prompt,
            generated_caption=caption
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate prompt: {str(e)}"
        )


@router.delete(
    "/",
    response_model=dict,
    summary="Delete all holidays",
    description="⚠️ WARNING: This will delete ALL holidays from the database. Use with caution!"
)
async def delete_all_holidays():
    """Delete all holidays (use with caution)."""
    return await HolidayRepository.delete_all()
