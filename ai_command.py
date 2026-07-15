# ai_command.py

import asyncio
import json
import traceback
from datetime import datetime
from typing import Optional, List, Dict, Any, Callable

import google.generativeai as genai
from google.generativeai.types import FunctionDeclaration, Tool
import speech_recognition as sr
import pyttsx3

from prometheus_core import Prometheus

# (Helper functions like _continuous_spinner_animation, make_hashable remain the same)
async def _continuous_spinner_animation(stop_event: asyncio.Event, message_prefix: str = "AI is processing..."):
    animation_chars = ["|", "/", "-", "\\"]
    idx = 0
    try:
        while not stop_event.is_set():
            print(f"\r{message_prefix} {animation_chars[idx % len(animation_chars)]}  ", end="", flush=True)
            idx += 1
            await asyncio.sleep(0.1)
    except asyncio.CancelledError: pass
    finally:
        if stop_event.is_set(): print(f"\r{message_prefix} Done!          ", end="", flush=True)
        else: print(f"\r{' ' * (len(message_prefix) + 20)}\r", end="", flush=True)

def make_hashable(obj):
    if "MapComposite" in str(type(obj)): obj = dict(obj)
    elif "RepeatedComposite" in str(type(obj)): obj = list(obj)
    if isinstance(obj, dict): return tuple((k, make_hashable(v)) for k, v in sorted(obj.items()))
    if isinstance(obj, list): return tuple(make_hashable(e) for e in obj)
    return obj

def load_system_prompt(file_path="system_prompt.txt") -> str:
    default_prompt = """You are Prometheus, an expert financial AI assistant. Your sole purpose is to help users by accurately and efficiently using the available tools. You must be direct, autonomous, and tool-focused.

Today's date is {current_date_for_ai_prompt}.

**--- CORE DIRECTIVES ---**
1.  **TOOL-FIRST MENTALITY:** Your primary response to ANY user request is to identify and use a tool. Do not chat. Execute a tool.
2.  **DIRECT QUESTIONS -> DIRECT TOOLS:** If a user asks a specific question, you MUST use the corresponding specific tool.
3.  **AUTONOMY IS PARAMOUNT:** Your most important task is to figure out how to complete a request without asking the user for more information. If you need information, there is likely a tool to get it.

**--- ADVANCED TOOL CHAINING WORKFLOW (CRITICAL EXAMPLE) ---**
You MUST follow this exact pattern for any request that requires gathering information before acting.

**Scenario:** The user says, "Run a volatility assessment on my favorite stocks."

**Execution:**
*   **TURN 1: Get the data.** Your Action: Call `get_user_preferences_tool()`.
*   **TURN 2: Use the data.** The system will provide you with the tickers. Your Action: Call `handle_assess_command` with the tickers you received.

**DO NOT** ask the user "What are your favorite stocks?". Follow the **Turn 1 -> Turn 2** pattern.

**Date Handling & Defaults:**
- If the user says "today" for any date parameter, use today's date in MM/DD/YYYY format: {current_date_mmddyyyy_for_ai_prompt}.
"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f: return f.read()
    except (FileNotFoundError, IOError):
        try:
            with open(file_path, 'w', encoding='utf-8') as f_create: f_create.write(default_prompt)
        except Exception: pass
        return default_prompt

def initialize_ai_components(api_key: str, main_globals: Dict[str, Any]):
    gemini_model = None
    if not api_key or "AIza" not in api_key: print("⚠️ Warning: Gemini API key is missing or invalid.")
    else:
        try:
            genai.configure(api_key=api_key)
            gemini_model = genai.GenerativeModel('gemini-2.0-flash-lite')
            print("✔ Gemini API configured successfully.")
        except Exception as e: print(f"❌ Error configuring Gemini API: {e}")
    tts_engine = None # TTS setup omitted for brevity

    function_names_for_ai = ["handle_briefing_command", "handle_risk_command", "handle_assess_command", "get_user_preferences_tool", "handle_sentiment_command", "handle_powerscore_command", "handle_favorites_command", "handle_feedback_command", "create_dynamic_investment_plan"]
    for key, val in main_globals.items():
        if key.startswith("handle_") and key.endswith("_command") and key not in function_names_for_ai and callable(val):
            function_names_for_ai.append(key)
    available_functions = {name: main_globals[name] for name in function_names_for_ai if name in main_globals}
    
    tool_declarations = [
        FunctionDeclaration(name="handle_powerscore_command", description="Generates a 'PowerScore' (0-100) for a stock.", parameters={"type": "object", "properties": {"ticker": {"type": "string"}, "sensitivity": {"type": "string", "enum": ["1", "2", "3"]}}, "required": ["ticker"]}),
        FunctionDeclaration(name="handle_sentiment_command", description="Performs AI sentiment analysis for a stock.", parameters={"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]}),
        FunctionDeclaration(name="handle_favorites_command", description="Manages the user's favorite tickers.", parameters={"type": "object", "properties": {"action": {"type": "string", "enum": ["view", "add", "remove"]}, "tickers": {"type": "array", "items": {"type": "string"}}}, "required": ["action"]}),
        FunctionDeclaration(name="handle_assess_command", description="Performs a volatility assessment on stocks.", parameters={"type": "object", "properties": {"assess_code": {"type": "string", "enum": ["A"]}, "tickers_str": {"type": "string"}, "timeframe_str": {"type": "string", "enum": ["1Y", "3M", "1M"]}}, "required": ["assess_code", "tickers_str"]}),
        FunctionDeclaration(name="get_user_preferences_tool", description="Retrieves the user's saved preferences, including 'favorite_tickers'."),
        FunctionDeclaration(name="handle_risk_command", description="Performs a risk assessment of the overall market. Does not require a ticker.", parameters={"type": "object", "properties": {"assessment_type": {"type": "string", "enum": ["standard", "eod"]}}, "required": ["assessment_type"]}),
    ]

    for declaration in tool_declarations:
        if declaration.name in available_functions: setattr(available_functions[declaration.name], '_is_tool', declaration)
    for func_name, func_obj in available_functions.items():
        if not hasattr(func_obj, '_is_tool'):
            command_name = func_name.replace("handle_", "").replace("_command", "")
            setattr(func_obj, '_is_tool', FunctionDeclaration(name=func_name, description=f"Runs the synthesized command '{command_name}'.", parameters={"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]}))
    return gemini_model, tts_engine, available_functions

async def handle_ai_prompt(
    user_new_message: str, is_new_session: bool, original_session_request: Optional[str],
    conversation_history: List[Dict], gemini_model_obj, available_functions: Dict[str, Callable],
    session_request_obj: Dict, step_count_obj: Dict, prometheus_obj: Prometheus,
    func_to_command_map: Dict[str, str]
):
    if not gemini_model_obj or not prometheus_obj:
        print("Error: AI components not configured.")
        return

    tool_declarations = [getattr(func, '_is_tool') for func in available_functions.values() if hasattr(func, '_is_tool')]
    if not tool_declarations:
        print("CRITICAL ERROR: No tool declarations found.")
        return
    all_gemini_tools = Tool(function_declarations=tool_declarations)

    if is_new_session:
        conversation_history.clear()
        step_count_obj['value'] = 0
        session_request_obj['value'] = original_session_request or user_new_message
        base_prompt = load_system_prompt()
        formatted_prompt = base_prompt.format(
            current_date_for_ai_prompt=datetime.now().strftime('%B %d, %Y'),
            current_date_mmddyyyy_for_ai_prompt=datetime.now().strftime('%m/%d/%Y')
        )
        conversation_history.extend([{"role": "user", "parts": [{"text": formatted_prompt}]}, {"role": "model", "parts": [{"text": "Acknowledged. I am Prometheus. Ready to execute commands."}]}])

    # --- FIX: Robust Context Injection Logic for Tool Chaining ---
    print(f"[AI DEBUG] Start of turn. User message: '{user_new_message}'")
    if conversation_history and len(conversation_history) > 2:
        last_entry = conversation_history[-1]
        if last_entry.get("role") == "tool":
            tool_response_part = last_entry.get("parts", [{}])[0]
            function_response = tool_response_part.get("function_response", {})
            tool_name = function_response.get("name")
            
            print(f"[AI DEBUG] Last action was a tool call to '{tool_name}'.")
            if tool_name == "get_user_preferences_tool":
                response_data = function_response.get("response", {})
                if response_data and response_data.get("status") == "success":
                    prefs = response_data.get("preferences", {})
                    fav_tickers = prefs.get("favorite_tickers")
                    if fav_tickers:
                        # This is the critical state management step.
                        context_message = f"CONTEXT: The user's favorite tickers have been retrieved and are: {', '.join(fav_tickers)}. Now, using this explicit list of tickers, you MUST fulfill the user's original request which was: '{session_request_obj['value']}'."
                        user_new_message = context_message
                        print(f"[AI DEBUG] CONTEXT INJECTION: Forcing new prompt: '{user_new_message}'")
                    else:
                        print(f"[AI DEBUG] Tool succeeded but no favorite tickers found. Proceeding normally.")
                else:
                    print(f"[AI DEBUG] `get_user_preferences_tool` call failed or returned empty. Proceeding normally.")


    conversation_history.append({"role": "user", "parts": [{"text": user_new_message}]})

    max_internal_turns, final_text_response = 10, ""
    stop_spinner_event = asyncio.Event()
    spinner_task = asyncio.create_task(_continuous_spinner_animation(stop_spinner_event, "AI is processing..."))
    try:
        for turn_num in range(max_internal_turns):
            response = await asyncio.to_thread(gemini_model_obj.generate_content, contents=conversation_history, tools=[all_gemini_tools])
            candidate = response.candidates[0] if response.candidates else None
            if not candidate or not candidate.content or not candidate.content.parts:
                final_text_response = "AI returned an empty response."
                break
            part = candidate.content.parts[0]
            if part.function_call and part.function_call.name:
                fc = part.function_call
                tool_name, tool_args = fc.name, dict(fc.args)
                conversation_history.append({"role": "model", "parts": [{"function_call": fc}]})
                if tool_name in available_functions:
                    try:
                        command_for_prometheus = func_to_command_map.get(tool_name)
                        if not command_for_prometheus: raise ValueError(f"No command mapping for function '{tool_name}'.")
                        result = await prometheus_obj.execute_and_log(command_name=command_for_prometheus, ai_params=tool_args)
                        conversation_history.append({ "role": "tool", "parts": [{"function_response": { "name": tool_name, "response": result if isinstance(result, dict) else {"summary": str(result)}}}]})
                    except Exception as e:
                        conversation_history.append({"role": "tool", "parts": [{"function_response": {"name": tool_name, "response": {"error": traceback.format_exc()}}}]})
                else:
                    conversation_history.append({"role": "tool", "parts": [{"function_response": {"name": tool_name, "response": {"error": f"Unknown tool '{tool_name}'."}}}]})
            elif part.text:
                final_text_response = part.text
                conversation_history.append({"role": "model", "parts": [{"text": final_text_response}]})
                break
            else: break
        else: final_text_response = "Max processing turns reached."
    finally:
        stop_spinner_event.set()
        await spinner_task
    print("\n--- Prometheus AI's Final Answer ---")
    print(final_text_response or "AI processing complete.")
    print("-----------------------------\n")

def speak_text(text: str, tts_engine_obj):
    """Converts a text string to speech and plays it."""
    if not tts_engine_obj:
        print("AI Response (TTS engine unavailable):", text)
        return
    try:
        tts_engine_obj.say(text)
        tts_engine_obj.runAndWait()
    except Exception as e:
        print(f"❌ Error during text-to-speech playback: {e}")
        print("AI Response (TTS failed):", text)

def listen_for_voice() -> Optional[str]:
    """Listens for voice input from the microphone."""
    if not sr:
        print("Voice recognition library 'SpeechRecognition' not available.")
        return None
    r = sr.Recognizer()
    with sr.Microphone() as source:
        r.pause_threshold = 1.0
        r.adjust_for_ambient_noise(source, duration=1)
        print("🎤 Listening for wake word ('Prometheus')...")
        try:
            audio = r.listen(source, timeout=10, phrase_time_limit=15)
        except sr.WaitTimeoutError:
            return None
    try:
        text = r.recognize_google(audio)
        print(f"👂 Heard: \"{text}\"")
        return text
    except sr.UnknownValueError:
        return None
    except sr.RequestError as e:
        print(f"Could not request results from Google Speech Recognition service; {e}")
        return None


async def parse_and_execute_voice_command(
    command_text: str, conversation_history: List[Dict], gemini_model_obj,
    available_functions: Dict[str, Callable], tts_engine_obj,
    session_request_obj: Dict, step_count_obj: Dict, prometheus_obj: Prometheus,
    func_to_command_map: Dict[str, str]
):
    """Parses transcribed text and calls the AI handler."""
    is_new = not conversation_history
    await handle_ai_prompt(
        user_new_message=command_text, is_new_session=is_new,
        original_session_request=command_text if is_new else session_request_obj['value'],
        conversation_history=conversation_history, gemini_model_obj=gemini_model_obj,
        available_functions=available_functions, session_request_obj=session_request_obj,
        step_count_obj=step_count_obj, prometheus_obj=prometheus_obj,
        func_to_command_map=func_to_command_map
    )
    final_response = "Request complete."
    if conversation_history:
        final_model_responses = [p.get("text", "") for e in reversed(conversation_history) if e.get("role") == "model" for p in e.get("parts", []) if "text" in p]
        if final_model_responses:
            final_response = final_model_responses[0]
    speak_text(final_response, tts_engine_obj)


async def handle_voice_command(
    conversation_history: List[Dict], gemini_model_obj, available_functions: Dict[str, Callable],
    tts_engine_obj, session_request_obj: Dict, step_count_obj: Dict, prometheus_obj: Prometheus,
    func_to_command_map: Dict[str, str]
):
    """Main loop for the voice assistant."""
    if not all([sr, tts_engine_obj]):
        print("❌ Cannot start voice assistant. Libraries missing or failed to initialize.")
        return

    speak_text("Voice assistant activated.", tts_engine_obj)
    while True:
        transcribed = listen_for_voice()
        if transcribed:
            text_lower = transcribed.lower()
            if any(phrase in text_lower for phrase in ["stop listening", "end conversation"]):
                speak_text("Deactivating voice assistant.", tts_engine_obj)
                break
            
            if "Prometheus" in text_lower:
                command = text_lower.split("Prometheus", 1)[-1].strip()
                if command:
                    speak_text(f"Processing: {command}", tts_engine_obj)
                    await parse_and_execute_voice_command(
                        command, conversation_history, gemini_model_obj, available_functions,
                        tts_engine_obj, session_request_obj, step_count_obj, prometheus_obj,
                        func_to_command_map=func_to_command_map
                    )
                else:
                    speak_text("Yes?", tts_engine_obj)