# LLM demo: Requirements LLM + Completeness LLM

First working piece of the agentic AI layer (Charlie and Colin). Runs an open-source
model through Ollama on the VM's GPU.

- `extract` — the Requirements LLM reads a funding announcement (PDF or .txt) and saves
  its requirements to `requirements.json`, each with a quote from the source.
- `check` — the Completeness LLM checks an application against those requirements and
  marks each one pass, fail, or unclear.

All sample applications are fake test data. Do not put real personal data on the VM.

## Setup (one time, on the VM)

```bash
# 1. Install Ollama (runs as a background service afterwards)
curl -fsSL https://ollama.com/install.sh | sh

# 2. Download a model (~5 GB)
ollama pull llama3.1:8b

# 3. Python environment
sudo apt install -y python3-venv
cd ~/llm
python3 -m venv venv
source venv/bin/activate
pip install requests pypdf
```

## Run

```bash
cd ~/llm
source venv/bin/activate

python3 grant_checker.py extract samples/announcement_sample.txt
python3 grant_checker.py check samples/application_bad.txt
python3 grant_checker.py check samples/application_good.txt
```

Use a real PDF announcement (for example, from Nina's sample PDFs):

```bash
python3 grant_checker.py extract path/to/announcement.pdf
```

Try a different model:

```bash
ollama pull qwen2.5:14b
python3 grant_checker.py --model qwen2.5:14b extract samples/announcement_sample.txt
```

While a command runs, `ollama ps` in a second terminal shows the model loaded on the GPU.

## Next steps

- Connect the check step to Nina and Shiyu's embedding search so the models pull
  rules from the database instead of one file.
- Add a judge step that routes requests to the right specialized LLM.
- Run the same tests through a commercial API by swapping `ask_llm()`, then compare
  accuracy and speed.
