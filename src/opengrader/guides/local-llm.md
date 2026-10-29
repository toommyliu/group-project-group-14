# Running a local AI model with Opengrader

Opengrader can read handwriting and suggest grades with a model that runs on
your own computer, so nothing about a student leaves it. Opengrader does not
install, start or download that model for you. You set it up once, by hand,
and Opengrader connects to it.

Be realistic about quality. Models small enough for a laptop read handwriting
and grade written answers noticeably worse than the large hosted assistants.
For most teachers the better route is an AI assistant working in a redacted
packet (`opengrader ai-readme`). Choose a local model when nothing may leave
the computer.

## 1. Install Ollama

- **macOS:** download it from https://ollama.com/download, or `brew install ollama`.
- **Windows:** the installer from https://ollama.com/download.
- **Linux:** `curl -fsSL https://ollama.com/install.sh | sh`.

You need about 16 GB of RAM. Apple Silicon or a GPU makes it much faster.

## 2. Start it

The Ollama desktop app starts the server when it opens. Without the app, run:

```
ollama serve
```

and leave that window open. It answers on `http://127.0.0.1:11434`.

## 3. Download the models

Opengrader's defaults (they need several GB of disk):

```
ollama pull {vision_model}      # reads handwriting
ollama pull {text_model}    # suggests grades for written answers
```

Any other Ollama model works. Type its name into AI setup instead.

## 4. Point Opengrader at it

In the app: **Tools → AI setup**.

1. Provider: **Ollama on this computer (offline)**, the default.
2. Server: leave it blank for `127.0.0.1:11434`, or type the address of an
   Ollama server elsewhere on your network.
3. Models: leave them blank for the defaults above, or type the ones you pulled.
4. Tick what the AI should do: *Read handwriting*, *Suggest grades*.
5. **Test connection**, then **Save**.

From a terminal, `opengrader doctor` checks the same things and prints the
command that fixes each problem. Every tool then uses the setting:
`opengrader run --llm`, `opengrader submission-extractor --llm-responses`,
`opengrader automated-grading --llm`.

If a run says the AI is not answering, start Ollama (step 2) and run it again.
Opengrader never starts Ollama itself.
