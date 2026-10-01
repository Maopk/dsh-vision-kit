# Running a local vision model for DSH on Windows (Ollama)

Why bother: DSH's vision-router talks to several backends; the free cloud tier
answered **HTTP 429 on effectively every call** from this machine, and the local
Ollama backend is what actually served the screenshots.

Everything below was hit for real on Windows 11 + an RTX 5060 Laptop (8 GB) with
Ollama 0.35.0. The failure modes are the useful part.

## Install

```powershell
winget install --id Ollama.Ollama -e      # or run OllamaSetup.exe /VERYSILENT /NORESTART
ollama pull qwen2.5vl:3b                  # 3.2 GB — the model used here
# alternatives:
ollama pull qwen2.5vl:latest              # 6.0 GB, 7b — same speed class, more VRAM
ollama pull granite3.2-vision:2b          # 2.4 GB — grounding is NOT better (see the report)
```

## Five things that will bite you

### 1. `ollama serve` dies in a crash loop if its model directory is unwritable

The tray app stores its settings in `%LOCALAPPDATA%\Ollama\db.sqlite`. If the
`models` row points at a directory the user cannot write (here: a read-only
`D:\ollama`), the server exits immediately, the CLI reports
`existing instance found, exiting` + `Error: timed out waiting for server to start`,
and `%LOCALAPPDATA%\Ollama\app.log` repeats:

```
level=ERROR source=server.go:224 msg="ollama exited" err="exit status 1"
```

and `%LOCALAPPDATA%\Ollama\server.log` has the real cause:

```
Error: mkdir D:\ollama\blobs: Access is denied.: ensure path elements are traversable
```

Fix the setting (back the DB up first), then set the env var as well:

```powershell
python -c "import sqlite3,os; p=os.path.expandvars(r'%LOCALAPPDATA%\Ollama\db.sqlite'); c=sqlite3.connect(p); c.execute('update settings set models=?, context_length=? where id=1', (r'C:\Users\<you>\.ollama\models', 16384)); c.commit()"
[Environment]::SetEnvironmentVariable('OLLAMA_MODELS', "$env:USERPROFILE\.ollama\models", 'User')
```

### 2. Killing `ollama serve` leaves orphaned `llama-server.exe` holding the VRAM

This is the big one. After killing the parent, the runner children survive and
keep the model resident — measured **7.6 GB of 8.15 GB** still held, with one
orphan burning 1374 s of CPU. Every subsequent load then thrashes, and calls stop
at suspiciously round deadlines (60 s, 120 s) as if the timeout were the problem.

Check and clear it:

```powershell
ollama ps                      # what the server thinks is loaded
nvidia-smi                     # what the GPU actually holds — compare the two
pwsh -File tools/reset-ollama.ps1
```

`reset-ollama.ps1` kills every `ollama`/`llama-server` process, waits for VRAM to
drain, starts exactly one detached `ollama serve`, and warms the model. After it:
VRAM back to ~150 MiB, first call 2–3 s instead of a timeout.

### 3. Context length: the default 4096 is too small for image prompts

With the default, the router's request failed with
`400 request (5389 tokens) exceeds the available context size (4096 tokens)`.
Set it once and restart the server:

```powershell
[Environment]::SetEnvironmentVariable('OLLAMA_CONTEXT_LENGTH', '16384', 'User')
```

`ollama ps` should then show `CONTEXT 16384`.

### 4. Do not put the server's log in `%TEMP%`

A background job whose log lived in `%TEMP%` produced a log that later vanished
and never started the pull process. Use a stable directory (here
`C:\Users\<you>\ollama-setup\`) with a `.cmd` wrapper that sets the env vars and
appends to a log file.

### 5. Start the server first, then pull

Pulling before the API answers gives
`ollama server not ready after retries` / `timed out waiting for server to start`.
Start detached, poll `http://127.0.0.1:11434/api/version` until it answers
(4 s when healthy), and only then pull. Interrupted pulls resume from the
`blobs\sha256-*-partial` file — no need to delete anything.

## Verify it actually works

```powershell
ollama ps                                   # model loaded, 100% GPU, CONTEXT 16384
Invoke-RestMethod http://127.0.0.1:11434/api/version

# end-to-end through the router's own log:
Get-Content "$env:USERPROFILE\.dsh\logs\vision-router\vision-router.log" -Tail 20 |
  Select-String 'backend success|attempt|failed'
```

A healthy local call looks like
`vision backend success [vision-http/local-ollama/qwen2.5vl:3b] latencyMs=2019`.

## Measured, on a clean GPU

| | qwen2.5vl:3b | qwen2.5vl:latest (7b) |
|---|---|---|
| VRAM with 16384 ctx | 3.5 GB | 6.9 GB |
| Load time (cold) | 8.2 s | — |
| 0.23 MP crop (direct HTTP) | 5.4 s / 11.3 s | 11.3 s |
| 0.66 MP full frame (direct HTTP) | 3.9 s | 5.0 s |
| Through the router (warm) | **2.0–2.4 s** | — |
| GPU utilization | 100% | 75% (spilled to CPU) |
