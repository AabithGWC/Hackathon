"""
Interactive CLI for chatting with the 7-Agent Financial AI Suite.
Usage:
    python engine/chat.py
"""
import sys
import os

# Ensure project root is in sys.path
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from engine.chatbot import AgentChatbot, ALL_PLAYBOOKS


def main():
    print("==================================================================")
    print("      7-Agent Executive Financial AI Suite - Chatbot Explainer     ")
    print("==================================================================")
    print("Connected to SQLite Database (`agent_runs.db`).")
    print("Ask questions about reconciliation breaks, cashflow forecasts, cost of funds,")
    print("investor reports, or executive summaries across all 7 agents.")
    print("Type 'exit' or 'quit' to end the session.\n")

    chatbot = AgentChatbot()
    history = []

    while True:
        try:
            user_input = input("\n[User] > ").strip()
            if not user_input:
                continue
            if user_input.lower() in ["exit", "quit"]:
                print("Exiting 7-Agent Chatbot Explainer. Goodbye!")
                break

            print("[Thinking] Fetching latest outcomes from SQLite & querying Groq LLM...")
            res = chatbot.ask(user_input, chat_history=history)

            print(f"\n[AI Explainer]:\n{res['reply']}")
            if res.get("playbooks_referenced"):
                print(f"\n(Data sources referenced: {', '.join(res['playbooks_referenced'])})")

            history.append({"role": "user", "content": user_input})
            history.append({"role": "assistant", "content": res["reply"]})

        except KeyboardInterrupt:
            print("\nSession ended by user.")
            break
        except Exception as e:
            print(f"\n[Error]: {e}")


if __name__ == "__main__":
    main()
