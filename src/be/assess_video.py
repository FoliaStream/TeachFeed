import time
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

import time
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

MODEL = "gemini-3.6-flash"   # check the current name in the AI Studio model list

class Comment(BaseModel):
    timestamp: str = Field(description="Moment in the video, format MM:SS")
    category: str = Field(description="One of: language, delivery, content, structure, other")
    quote: str = Field(description="The exact words the student said at that moment")
    issue: str = Field(description="What is wrong or could be better")
    correction: str = Field(description="The corrected or improved version")
    explanation: str = Field(description="One or two sentences explaining why")

class Report(BaseModel):
    summary: str
    strengths: list[str]
    areas_to_improve: list[str]
    comments: list[Comment]

SYSTEM = """You assist a teacher in assessing a student's video.
Rules:
- Base every comment on what is actually said or shown in the video. Never invent mistakes.
- Quote the student's exact words and give the timestamp as MM:SS.
- If you are unsure of the exact second, give your best estimate.
- The teacher's notes describe context, tone and focus. Use them to decide emphasis and how
  kindly to phrase things, but never to change or invent findings.
- Write the feedback in English, addressed to the teacher."""

def _upload_and_wait(client, path, timeout=300):
    f = client.files.upload(file=path)
    start = time.time()
    while f.state.name == "PROCESSING":
        if time.time() - start > timeout:
            raise TimeoutError("Video processing took too long.")
        time.sleep(2)
        f = client.files.get(name=f.name)
    if f.state.name != "ACTIVE":
        raise RuntimeError(f"Video could not be processed (state: {f.state.name}).")
    return f

def assess_video(api_key, video_path, task_description="", teacher_note="") -> Report:
    client = genai.Client(api_key=api_key)
    video = _upload_and_wait(client, video_path)
    try:
        prompt = (
            f"Task given to the student:\n{task_description or '(not provided)'}\n\n"
            f"Teacher's notes:\n{teacher_note or '(none)'}\n\n"
            "Assess the video."
        )
        response = client.models.generate_content(
            model=MODEL,
            contents=[video, prompt],
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM,
                response_mime_type="application/json",
                response_schema=Report,
                temperature=0.2,
            ),
        )
        if response.parsed is None:
            raise ValueError(f"Could not parse the model output:\n{response.text}")
        return response.parsed
    finally:
        client.files.delete(name=video.name)   # remove the uploaded copy from Google's storage