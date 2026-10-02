import ollama

MODEL = "llama3.1:latest"


def main():
    response = ollama.chat(
        model=MODEL,
        messages=[{"role": "user", "content": "Reply with a short greeting confirming you're working."}],
    )
    print(response["message"]["content"])


if __name__ == "__main__":
    main()
