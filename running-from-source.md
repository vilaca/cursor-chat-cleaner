# Running from source

Running from source requires macOS and Python 3.10 or newer.

Clone the repository and enter its directory:

```bash
git clone https://github.com/vilaca/cursor-chat-cleaner.git
cd cursor-chat-cleaner
```

Create a virtual environment and install the project:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Verify the command:

```bash
cursor-chat-cleaner --version
cursor-chat-cleaner --help
```

For later sessions, reactivate the environment:

```bash
source .venv/bin/activate
```

Run without installing:

```bash
PYTHONPATH=src python3 -m cursor_chat_cleaner --help
```

Run the tests:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

Leave the virtual environment with `deactivate`.
