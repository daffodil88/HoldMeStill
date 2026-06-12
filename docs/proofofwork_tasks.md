# Writing a Proof of Work Task

Proof of Work is one of the games shipped with HoldMeStill. Unlike the other games, the player does not play a round in the browser — they complete a real-world task (a maths sheet, a tidy room, a written reflection, a piece of code) and upload their work. An AI evaluator reads the submission and decides how many seconds to take off the lock timer (good work) or add to it (poor or fake work).

What the player is asked to do is defined entirely by YAML files. You write a YAML file, drop it into `pi/games/proofofwork/tasks/`, restart the server, and the new task appears in the task picker. No code changes are needed.

This guide is the full reference for those YAML files. Read it once end to end, then use it like a cookbook.

## Where task files live

```
pi/games/proofofwork/
├── proofofwork.py
├── proofofwork.js
├── style.css
├── config
├── tasks.conf
└── tasks/
    ├── math.yaml
    ├── clean_room.yaml
    ├── reflection.yaml
    └── ...one .yaml file per task
```

Each `.yaml` file in `tasks/` defines one selectable task. The filename without the extension becomes the task ID (e.g. `math.yaml` → task ID `math`). Task IDs only appear in `tasks.conf` and in URLs — players see the `subject:` field instead.

To add a new task: create `pi/games/proofofwork/tasks/your_task.yaml`, fill it in following the schema below, and restart the server. To remove a task, delete the file (or disable it in `tasks.conf` — see the end of this guide).

## How a task is played

1. The player opens Proof of Work and sees a grid of cards, one per task file. Each card shows the subject, a short description, and the maximum time reduction and addition.
2. The player picks a task. The server loads the YAML, generates any AI-generated content (e.g. fresh maths problems), and shows the task page.
3. The task page shows: a "Before submitting" checklist (if you wrote requirements), a numbered list of scored dimensions (if you wrote criteria), and each task section in turn — heading, questions, generated content, player notes.
4. The player writes answers in a text box and/or uploads files (photos, PDFs, screenshots — anything).
5. The evaluator reads the submission and produces a score from 0 to 100. The score is converted into a time adjustment using your `max_time_reduction` and `max_time_addition` limits, and the lock timer changes accordingly.

You control everything in steps 1, 2, 3 and the limits in step 5 through the YAML.

## The two evaluation modes

Before the field reference: every task runs in one of two evaluation modes, and which mode you are in changes how some fields behave. Pick the right mode before you start writing.

**Question mode.** The questions themselves are the scored units. The evaluator places one tick or cross per question and the score is the percentage of ticks. Use this when each item has a clear right answer or clear expected content — maths problems, bash commands, theory questions, a list of generated problems the player solves.

**Named mode.** You define a fixed list of scored *dimensions* (e.g. "Authenticity", "Depth of thought", "Floor clear"). The evaluator places one tick or cross per dimension. Use this when the submission is one piece of work judged on several qualitative aspects — a diary entry, a tidy-room photo set, a written reflection.

You choose the mode by whether you include a top-level `criteria:` list. If `criteria:` is present, you are in named mode. If not, you are in question mode.

```yaml
criteria:                          # ← presence of this list = NAMED mode
  - "Floor clear — ..."
  - "Bed made — ..."

# vs.

tasks:                             # ← no criteria list at the top level = QUESTION mode
  - title: Algebra Problems
    questions:
      - "Solve 3x + 7 = 22"
      - "..."
```

Why this matters: in named mode, any `questions:` you put inside task sections are shown to the player as a checklist but are **not** scored and are **not** sent to the evaluator. The criteria list is what gets scored. If you want to tell the player what to do in named mode, use `player_notes:` instead.

## YAML schema — top-level fields

These fields live at the top of the file, outside any `tasks:` section.

### Required

```yaml
subject: str                       # display title shown on the task card and task page
max_time_reduction: int            # seconds; max amount taken off the lock timer on a strong submission
max_time_addition: int             # seconds; max amount added to the lock timer on a poor submission
tasks: list                        # one or more task sections (see "Task section fields" below)
```

The two `max_*` values are absolute caps. A perfect submission removes exactly `max_time_reduction` seconds; a submission that scores zero adds exactly `max_time_addition` seconds. Most submissions fall between the two extremes — see "How scoring works" for the exact formula.

### Optional

```yaml
description: str                   # short blurb shown on the task card; aim for ≤ 20 words
criteria: [str, ...]               # named-mode scored dimensions (see "The two evaluation modes")
evaluator_notes: str               # private hints to the AI evaluator; not shown to the player
requirements: [str, ...]           # hard gates shown to the player as a "Before submitting" checklist
verify_novelty: bool | "files_only"  # block duplicate submissions; default false
```

A few notes on each:

- **`description`** is the one or two sentences the player reads to decide whether to pick this task. It only appears on the picker tile.
- **`criteria`** turns the task into named mode (see above). Write each item as `"Short label — what counts as good"`; the part before the em dash becomes the heading and the part after becomes the rubric the evaluator applies. Aim for 3–6 dimensions.
- **`evaluator_notes`** lets you whisper interpretation rules to the AI evaluator without showing them to the player. Use it for edge cases ("a minor arithmetic slip is still substantially correct if the method is right", "one tidy corner does not override a visibly messy floor"). Do not use it for hard gates (use `requirements`) or for defining what is scored (use `criteria` or `questions`).
- **`requirements`** are hard rules the player can check before they upload. Write each one as a single sentence ("At least 3 photos must be submitted"). The evaluator treats failure of any requirement as an automatic zero, so use this sparingly and only for things that genuinely disqualify the submission.
- **`verify_novelty`** is the duplicate-submission guard. See the next section.

### Duplicate submissions (`verify_novelty`)

By default the server does not check whether a submission repeats an earlier one. The same answer can be submitted twice and get credit twice. You can change this per task:

- `verify_novelty: true` — full novelty check. Identical text or identical file bytes are rejected before reaching the evaluator (instant maximum penalty, no AI call). Earlier text submissions are also fed to the evaluator so it can catch paraphrased repeats. Use this for written tasks where someone could try to resubmit the same answer.
- `verify_novelty: files_only` — file hash check only. Identical files are rejected; the text field is ignored for duplicate detection. Use this for photo-based tasks where the text box is incidental and only the uploaded evidence matters (e.g. "clean your room").
- `verify_novelty: false` (or omitted) — no novelty check.

History is kept in server RAM only and is cleared on restart. There is no on-disk record of past submissions.

## YAML schema — task section fields

The top-level `tasks:` field is a list. Each item is one *section* of the task page. A task can have one section or many — sections are displayed top to bottom, separated by their headings.

```yaml
tasks:
  - title: str                     # required; section heading
    questions: [str, ...]          # optional; numbered list shown to the player
    player_notes: str              # optional; plain paragraph of instructions to the player
    generate: str                  # optional; prompt sent to the AI to generate content at select time
    generate_count: int            # required when generate is present AND the generated content is scored
```

- **`title`** appears as a heading on the task page. Every section needs one.
- **`questions`** is a numbered list shown to the player. In **question mode**, each question is one scored unit. In **named mode**, the list is shown but ignored by the evaluator — write `player_notes:` instead if you want to give instructions in named mode.
- **`player_notes`** is a paragraph of free-text guidance shown to the player ("Write your full working", "Take 3–5 photos from different angles"). It is never sent to the evaluator — it is purely for the player.
- **`generate`** asks the AI to produce fresh content the moment the player selects the task. The classic use is generating maths problems so the player can't memorise them. Whatever the AI returns is shown in the section body as a paragraph. The prompt fires once per task selection; the player sees the same generated content for the duration of that task instance.
- **`generate_count`** tells the scoring system how many scored items the `generate` prompt produces. If you ask the AI to "create 5 algebra problems", set `generate_count: 5`. If the generated content is display-only (e.g. a scenario the player reads and then comments on in a separate section), omit `generate_count` or set it to 0.

**`generate_count` must match exactly.** If your prompt asks for 5 problems and you set `generate_count: 4`, the score denominator is wrong and the player either gets a free credit or an unfair penalty. Count what you ask for, and write that number.

## How scoring works

You do not need to understand this to write a task, but it helps when you are tuning `max_time_reduction` and `max_time_addition`.

1. The evaluator returns a feedback string containing one tick (✅) or cross (❌) per scored unit.
2. The score is `(number of ticks ÷ number of scored units) × 100`.
3. The number of scored units is fixed when the task is loaded:
   - Named mode: the length of the `criteria` list.
   - Question mode: the sum of `len(questions)` plus `generate_count` across every task section.
4. Compare the score against an evaluation threshold (default 75, set in the game's `config` file):
   - **Score ≥ 75 → good submission.** The lock timer is reduced. A score of 100 gives the full `max_time_reduction`; a score of 75 gives zero reduction; values in between scale linearly.
   - **Score < 75 → poor submission.** The lock timer is increased. A score of 0 gives the full `max_time_addition`; a score of 75 gives zero addition; values in between scale linearly.

There is no "cliff" at the threshold — both curves pass through zero at the threshold, so a borderline submission has near-zero impact.

Practical tuning advice:

- Pick `max_time_reduction` and `max_time_addition` based on how hard the task is and how much you trust the evaluator. A short reflection is worth less than thirty minutes of real cleaning.
- The addition is usually smaller than the reduction. A poor submission still required effort.
- If you find players are consistently scoring near the threshold and ending the session no better off, the task is either too hard or the criteria are too strict — soften the rubric.

## A complete example — question mode

This is the maths task. The player gets five fresh AI-generated algebra problems, writes their working in the text box, uploads nothing.

```yaml
subject: Mathematics – Algebra & Problem Solving
description: Solve algebra and problem-solving questions covering core mathematical concepts.
max_time_reduction: 1200
max_time_addition: 600

evaluator_notes: >
  A problem is substantially correct if the method is right and working is shown,
  even with a minor arithmetic slip. No working, a wild guess, or a wrong method
  counts as incorrect. One perfect answer must not compensate for three blank ones.

tasks:
  - title: Algebra Problems
    generate: >
      Create 5 algebra problems of increasing difficulty, suitable for a
      student aged 14–16. Include a mix of: solving linear equations,
      expanding brackets, factorising simple quadratics, and one word problem.
      Number them 1–5. Present only the problems, no solutions or hints.
    generate_count: 5

  - title: Your Working
    player_notes: >
      Write your full working and final answer for each problem. Show every step —
      do not skip lines. If you get stuck on a problem, explain where you got stuck
      and why.
```

Why this is question mode: there is no top-level `criteria:` list. The five generated problems are the scored units (`generate_count: 5`). The "Your Working" section is purely instructional — `player_notes` is shown to the player and ignored by the evaluator.

## A complete example — named mode

This is the tidy-room task. The player uploads photos and the evaluator judges the result against three dimensions.

```yaml
subject: Clean Your Room
description: Tidy your room and submit photo evidence showing the floor, bed, and surfaces.
max_time_reduction: 1800
max_time_addition: 600
verify_novelty: files_only

criteria:
  - "Floor clear — all clothes, bags, and rubbish removed from the floor"
  - "Bed made — bed is made or bedding is straightened"
  - "Surfaces clear — visible desk or surface clutter removed or organised"

requirements:
  - "At least 3 photos must be submitted"
  - "Photos must clearly show the same room (no downloaded or staged images)"

evaluator_notes: >
  Items shoved out of frame or hidden count as not done.
  One tidy corner must not override a visibly messy floor.
  A few wrinkles on the bedding are still a pass.

tasks:
  - title: Tidy Up Your Room
    player_notes: >
      Take 3–5 photos from different angles showing the result, then upload them
      as your proof of work.
```

Why this is named mode: the top-level `criteria:` list defines the three scored dimensions. The single task section has no `questions:` and no `generate:` — it is only there to give the player instructions. `verify_novelty: files_only` stops the player resubmitting yesterday's photos.

## Which field do I use?

| I want to…                                          | Use                  |
|-----------------------------------------------------|----------------------|
| Tell the player what to do in this section          | `player_notes`       |
| Tell the player what counts as a hard requirement   | `requirements`       |
| Tell the AI evaluator how to interpret edge cases   | `evaluator_notes`    |
| Define what dimensions are scored (named mode)      | `criteria`           |
| List the questions the AI will mark (question mode) | `questions`          |
| Generate fresh content per attempt                  | `generate` (+ `generate_count` if scored) |

## Controlling task order — `tasks.conf`

`pi/games/proofofwork/tasks.conf` controls the order tasks appear in the picker and lets you disable a task without deleting its file. The format is one task ID per line:

```
reflection
clean_room
oop_python
bash_cli
math
```

Wrap an ID in square brackets to disable it:

```
[math]
```

Disabled tasks are hidden from the player but the YAML file stays in place. Tasks not listed in `tasks.conf` are enabled and appear alphabetically after the listed ones. Comment lines (starting with `#`) and blank lines are ignored.

## Things to keep in mind

- **The server reads task files at startup.** Add, remove, or edit a YAML file and you need to restart the Flask server before the change takes effect.
- **YAML syntax mistakes show up as warnings on stderr.** If a task does not appear after a restart, check the server log. The most common errors are missing required fields and indentation that breaks the list structure.
- **Generated content is per attempt, not per session.** Each time a player selects a task with a `generate:` prompt, a fresh AI call produces new content. Generating costs an API call and adds a few seconds of delay to task selection.
- **The evaluator uses the same Anthropic API key as the game config.** Set `ANTHROPIC_API_KEY` in `pi/games/proofofwork/config` before any task can be played.
- **There is no "save draft" feature.** The player either submits or they don't. Design tasks the player can finish in one sitting.
- **`generate_count` mismatches are silent.** If your prompt asks for 5 items but you wrote `generate_count: 4`, nothing flags it — but the score will be wrong. Double-check this every time you change a `generate:` prompt.
