"""Local Gradio chat UI for the price-tracking agent, for trying it without the backend and frontend.

Run from the repo root:
    uv run python agent/gradio_app.py
It starts at http://127.0.0.1:7860 by default."""
import json
import logging

import gradio as gr
from dotenv import load_dotenv

load_dotenv()

import tracker  # noqa: E402 (after load_dotenv, so tracker sees the .env settings)

INITIAL_GREETING = [
    {
        "role": "assistant",
        "content": "👋 Hello! I am your Steam Price Tracking Assistant. Provide me with a game title, and I will instantly check its current deals and compare them to its historical all-time low to let you know if it's the right time to buy! Which game are we looking up today?"
    }
]


def message_text(content):
    if isinstance(content, str):
        return content
    return "".join(part.get("text", "") for part in content if part.get("type") == "text")


def plain_history(chatbot_history) -> list[dict]:
    # Gradio 6 returns each message's content as a list of parts, e.g. [{"type": "text", "text": "..."}]
    return [{"role": h["role"], "content": message_text(h["content"])} for h in chatbot_history]


def chatbot_message(reply: dict) -> dict:
    # Gradio shows a message's "options" as buttons under it; clicking one calls handle_option_select.
    # Each value is the choice to pass to tracker.select_option, so the click needs no other state.
    message = {"role": "assistant", "content": reply["content"]}
    choices = reply["choices"]
    if not choices:
        return message
    titles = [game["title"] for game in choices["games"]]
    options = []
    for i, game in enumerate(choices["games"]):
        label = game["title"]
        # Games can share a title, so number repeated titles to tell the buttons apart
        if titles.count(label) > 1:
            label = f"{label} ({titles[:i + 1].count(label)})"
        options.append({"label": label, "value": json.dumps({"type": "game", **game})})
    # The catalog only covers games searched before, so the right game may not be listed yet
    if choices["source"] == "catalog":
        options.append({"label": "None of these, search online",
                        "value": json.dumps({"type": "search_online", "query": choices["query"]})})
    elif choices["source"] == "rag":
        options.append({"label": "None of these", "value": json.dumps({"type": "none_of_these"})})
    return {**message, "options": options}


def chat(message, chatbot_history, country):
    if not message.strip():
        return "", chatbot_history
    history = plain_history(chatbot_history) + [{"role": "user", "content": message}]
    reply = tracker.final_reply(tracker.chat_turn(history, country))
    return "", history + [chatbot_message(reply)]


def handle_option_select(evt: gr.SelectData, chatbot_history, country):
    try:
        choice = json.loads(evt.value)
    except (TypeError, json.JSONDecodeError):
        return gr.skip()
    history = plain_history(chatbot_history)
    reply = tracker.final_reply(tracker.select_option(history, choice, country))
    return history + [{"role": "user", "content": tracker.selection_message(choice)}, chatbot_message(reply)]


with gr.Blocks(title="Game Price Tracker AI") as ui:
    gr.Markdown('# Steam Game Price tracker AI Agent')
    with gr.Row():
        with gr.Column(scale=3, min_width=500):
            chatbot = gr.Chatbot(value=INITIAL_GREETING, height=400)
            with gr.Row():
                txt_input = gr.Textbox(show_label=False, placeholder="Ask about a game", scale=4)
                submit_btn = gr.Button('Send', variant="primary", scale=1)
        with gr.Column(scale=1):
            gr.Markdown('### Control & Watchlist Panel')
            with gr.Accordion("⚙️ Advanced Options", open=True):
                country_code_dropdown = gr.Dropdown(choices=tracker.COUNTRIES, label="Select Country",
                                                    value=tracker.COUNTRIES[0], interactive=True)

    txt_input.submit(fn=chat, inputs=[txt_input, chatbot, country_code_dropdown], outputs=[txt_input, chatbot])
    submit_btn.click(fn=chat, inputs=[txt_input, chatbot, country_code_dropdown], outputs=[txt_input, chatbot])
    chatbot.option_select(fn=handle_option_select, inputs=[chatbot, country_code_dropdown], outputs=[chatbot])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    tracker.itad_api_key()  # stop now if the key is missing
    tracker.knowledge_base()
    ui.launch()
