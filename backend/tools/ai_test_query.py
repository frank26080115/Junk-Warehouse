"""Utility script to send a test query to an AI model defined by AiInstance."""
import argparse
import time

from backend.automation.ai_helpers import AiInstance

def main() -> None:
    """Parse arguments, run the AI query, and report the timing."""
    parser = argparse.ArgumentParser(
        description=(
            "Send a single user message to the configured AI provider while "
            "optionally supplying a custom system prompt."
        )
    )
    parser.add_argument(
        "--model-name",
        dest="model_name",
        default="",
        help=(
            "Name of the model to use. Defaults to a blank string when not "
            "supplied."
        ),
    )
    parser.add_argument(
        "--system-message",
        dest="system_message",
        default="",
        help="System prompt content sent before the user message.",
    )
    parser.add_argument(
        "--user-message",
        dest="user_message",
        default="",
        help="User prompt content to send to the model.",
    )

    args = parser.parse_args()

    # Instantiate AiInstance with the requested model name; this handles
    # connecting to either Ollama or OpenAI based on configuration.
    ai_instance = AiInstance(args.model_name)

    # Measure the time it takes for the AI service to respond.
    start_time = time.perf_counter()
    response_text = ai_instance.query(
        user_msg=args.user_message,
        system_msg=args.system_message,
    )
    elapsed_seconds = time.perf_counter() - start_time

    # Provide detailed output so that a user can see the response and timing.
    print("AI response:\r\n")
    print(response_text)
    print(
        "\r\nTotal request duration: {:.3f} seconds".format(elapsed_seconds)
    )


if __name__ == "__main__":
    # Entry point for command-line execution.
    main()
