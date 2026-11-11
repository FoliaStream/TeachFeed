# import time
# from google import genai
# from google.genai import types
# from pydantic import BaseModel, Field
# import re
# from datetime import date

# from src.be.model_router import generate_with_fallback

# class Comment(BaseModel):
#     timestamp: str = Field(description="Moment in the video, format MM:SS")
#     category: str = Field(description="One of: language, delivery, content, structure, other")
#     quote: str = Field(description="The student's exact words at that moment, including any mistakes")
#     issue: str = Field(description="What is wrong or could be better")
#     correction: str = Field(description="The corrected or improved version")
#     explanation: str = Field(description="One or two sentences explaining why")

# class Report(BaseModel):
#     summary: str
#     strengths: list[str]
#     areas_to_improve: list[str]
#     comments: list[Comment]

# SYSTEM = """You assist a teacher in assessing a student's video.
# Rules:
# - Base every comment on what is actually said or shown in the video. Never invent mistakes.
# - Quote the student's words exactly as spoken, including grammar mistakes. Never correct a quote;
#   put the correction in the correction field.
# - Give each timestamp as MM:SS. If you are unsure of the exact second, give your best estimate.
# - Go through the video in order, from the first second to the last, and comment on every mistake
#   or noteworthy moment you find. Do not stop after the first few.
# - Aim for at least one comment per minute of video, and more wherever there are many mistakes.
#   Spread comments across the whole video, not just the beginning.
# - If the same mistake repeats, give it one comment, and mention the other timestamps in the
#   explanation.
# - The teacher's notes describe context, tone and focus. Use them to decide emphasis and how
#   kindly to phrase things, but never to change or invent findings.
# - Write the feedback in the language the teacher asks for in their notes or extra instructions;
#   if none is requested, use English. Always keep transcript, quote and correction in the
#   language the student speaks, and keep category exactly as one of the listed English values."""

# def _upload_and_wait(client, path, timeout=300):
#     f = client.files.upload(file=path)
#     start = time.time()
#     while f.state.name == "PROCESSING":
#         if time.time() - start > timeout:
#             raise TimeoutError("Video processing took too long.")
#         time.sleep(2)
#         f = client.files.get(name=f.name)
#     if f.state.name != "ACTIVE":
#         raise RuntimeError(f"Video could not be processed (state: {f.state.name}).")
#     return f

# def _config(use_thinking=True):
#     return types.GenerateContentConfig(
#         system_instruction=SYSTEM,
#         response_mime_type="application/json",
#         response_schema=Report,
#         temperature=0.2,
#         max_output_tokens=8192,
#         thinking_config=types.ThinkingConfig(thinking_level="medium") if use_thinking else None,
#     )

# def assess_video(api_key, video_path, task_description="", teacher_note=""):
#     """Returns (report, usage, name of the model that answered)."""
#     client = genai.Client(
#         api_key=api_key.strip(),
#         http_options=types.HttpOptions(timeout=180_000),   # 3 minutes, in milliseconds
#     )
#     video = _upload_and_wait(client, video_path)
#     try:
#         prompt = (
#             f"Task given to the student:\n{task_description or '(not provided)'}\n\n"
#             f"Teacher's notes:\n{teacher_note or '(none)'}\n\n"
#             "Assess the whole video from start to end, with detailed, timestamped comments."
#         )

#         parsed, response, model = generate_with_fallback(client, [video, prompt], _config, Report)
#         return parsed, response.usage_metadata, model
#     finally:
#         client.files.delete(name=video.name)               # remove the uploaded copy from Google's storage


# def mmss_to_seconds(ts: str) -> int:
#     try:
#         parts = [int(p) for p in ts.strip().split(":")]
#     except ValueError:
#         return 0
#     seconds = 0
#     for p in parts:
#         seconds = seconds * 60 + p
#     return seconds



# def build_text_report(report: dict, request: dict) -> str:
#     title = "Video assessment"
#     if request["student_label"]:
#         title += f" - {request['student_label']}"

#     lines = [title, f"Date: {date.today():%d %B %Y}", ""]
#     if request["task_description"]:
#         lines += ["Task", request["task_description"], ""]
#     lines += ["Summary", report["summary"], ""]
#     lines += ["Strengths"] + [f"- {s}" for s in report["strengths"]] + [""]
#     lines += ["Areas to improve"] + [f"- {a}" for a in report["areas_to_improve"]]
#     lines += ["", "Detailed comments"]
#     for c in report["comments"]:
#         lines += [
#             f"[{c['timestamp']}] {c['category']}",
#             f'  Said: "{c["quote"]}"',
#             f"  Better: {c['correction']}",
#             "",
#         ]
#     return "\n".join(lines)
import time
from datetime import date

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from src.be.model_router import (
    generate_with_fallback,
    AssessmentError,
    PREFERRED_MODELS,
)


class Comment(BaseModel):
    timestamp: str = Field(description="Moment in the video, format MM:SS")
    category: str = Field(description="One of: language, delivery, content, structure, other")
    quote: str = Field(description="The student's exact words at that moment, including any mistakes")
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
- Quote the student's words exactly as spoken, including grammar mistakes. Never correct a quote;
  put the correction in the correction field.
- Give each timestamp as MM:SS. If you are unsure of the exact second, give your best estimate.
- Go through the video in order, from the first second to the last, and comment on every mistake
  or noteworthy moment you find. Do not stop after the first few.
- Aim for at least one comment per minute of video, and more wherever there are many mistakes.
  Spread comments across the whole video, not just the beginning.
- If the same mistake repeats, give it one comment, and mention the other timestamps in the
  explanation.
- The teacher's notes describe context, tone and focus. Use them to decide emphasis and how
  kindly to phrase things, but never to change or invent findings.
- Write the feedback in the language the teacher asks for in their notes or extra instructions;
  if none is requested, use English. Always keep transcript, quote and correction in the
  language the student speaks, and keep category exactly as one of the listed English values."""


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


def _config(use_thinking=True):
    return types.GenerateContentConfig(
        system_instruction=SYSTEM,
        response_mime_type="application/json",
        response_schema=Report,
        temperature=0.2,
        # 8192 was too small: on a video task the thinking tokens are billed against
        # this budget, so the model can spend it all on reasoning and return no text.
        max_output_tokens=32768,
        thinking_config=types.ThinkingConfig(thinking_level="low") if use_thinking else None,
    )


def _is_unreadable_answer(e):
    """True when the router stopped because the model produced no text at all."""
    return "could not be read" in str(e)


def _report_from_response(response, model_label):
    """Turn a raw SDK response into (Report, usage, model label). Raises AssessmentError on failure."""
    text = (response.text or "").strip()
    if not text:
        cands = response.candidates or []
        finish = getattr(cands[0], "finish_reason", "?") if cands else "?"
        um = response.usage_metadata
        
        # NEW: Check for safety blocks
        feedback = getattr(response, "prompt_feedback", None)
        block_reason = getattr(feedback, "block_reason", None) if feedback else "None"
        
        raise AssessmentError(
            "Gemini returned no text for this video. "
            f"finish_reason={finish}, "
            f"block_reason={block_reason}, "  # <-- Added this
            f"prompt_tokens={getattr(um, 'prompt_token_count', '?')}, "
            f"thoughts={getattr(um, 'thoughts_token_count', '?')}, "
            f"output={getattr(um, 'candidates_token_count', '?')}. "
            "The video may be too long, or its content may be flagged by the safety filter."
        )


def assess_video(api_key, video_path, task_description="", teacher_note=""):
    """Returns (report, usage, name of the model that answered)."""
    client = genai.Client(
        api_key=api_key.strip(),
        http_options=types.HttpOptions(timeout=180_000),   # 3 minutes
    )
    video = _upload_and_wait(client, video_path)
    try:
        prompt = (
            f"Task given to the student:\n{task_description or '(not provided)'}\n\n"
            f"Teacher's notes:\n{teacher_note or '(none)'}\n\n"
            "Assess the whole video from start to end, with detailed, timestamped comments."
        )
        try:
            parsed, response, model = generate_with_fallback(
                client, [video, prompt], _config, Report,
            )
            return parsed, response.usage_metadata, model
        except AssessmentError as e:
            if not _is_unreadable_answer(e):
                raise
            # The router stops on an unreadable answer. On a long video that
            # usually means the model returned no text (thoughts ate the budget),
            # so a retry without thinking is worth the cost.
            fallback_model = PREFERRED_MODELS[0]
            print(
                f"Router stopped on an unreadable answer; "
                f"retrying {fallback_model} without thinking.",
                flush=True,
            )
            try:
                response = client.models.generate_content(
                    model=fallback_model,
                    contents=[video, prompt],
                    config=_config(use_thinking=False),
                )
            except Exception as exc:
                raise AssessmentError(
                    f"Assessment failed, and the retry could not run: {exc}\n\n"
                    f"Original error:\n{e}"
                ) from exc
            return _report_from_response(response, f"{fallback_model} (no thinking)")
    finally:
        try:
            client.files.delete(name=video.name)               # remove the uploaded copy
        except Exception:
            pass


def mmss_to_seconds(ts: str) -> int:
    try:
        parts = [int(p) for p in ts.strip().split(":")]
    except ValueError:
        return 0
    seconds = 0
    for p in parts:
        seconds = seconds * 60 + p
    return seconds


def build_text_report(report: dict, request: dict) -> str:
    title = "Video assessment"
    if request["student_label"]:
        title += f" - {request['student_label']}"

    lines = [title, f"Date: {date.today():%d %B %Y}", ""]
    if request["task_description"]:
        lines += ["Task", request["task_description"], ""]
    lines += ["Summary", report["summary"], ""]
    lines += ["Strengths"] + [f"- {s}" for s in report["strengths"]] + [""]
    lines += ["Areas to improve"] + [f"- {a}" for a in report["areas_to_improve"]]
    lines += ["", "Detailed comments"]
    for c in report["comments"]:
        lines += [
            f"[{c['timestamp']}] {c['category']}",
            f'  Said: "{c["quote"]}"',
            f"  Better: {c['correction']}",
            "",
        ]
    return "\n".join(lines)