import json
import os

MEMORY_FILE = "memory.json"


def load_memory():

    if not os.path.exists(MEMORY_FILE):
        return {}

    with open(
        MEMORY_FILE,
        "r",
        encoding="utf-8"
    ) as f:

        return json.load(f)


def save_message(user_id, role, content):

    memory = load_memory()

    user_id = str(user_id)

    if user_id not in memory:
        memory[user_id] = []

    memory[user_id].append({
        "role": role,
        "content": content
    })

    memory[user_id] = memory[user_id][-10:]

    with open(
        MEMORY_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            memory,
            f,
            ensure_ascii=False,
            indent=4
        )


def get_history(user_id):

    memory = load_memory()

    return memory.get(str(user_id), [])