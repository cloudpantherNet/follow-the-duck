# Teacher operating guide (1 page)

## Before class

1. Power on the PiCar-X, wait ~30 s for Wi-Fi.
2. On the laptop: `ssh robot@classbot.local`, then
   `cd ~/follow-the-duck && .venv/bin/ftd doctor` — all green/WARN is fine,
   any `FAIL` means skip live driving today (the mock replay demo below works
   regardless).
3. Clear 2–3 m of flat floor. No stairs or table edges nearby. Keep the
   robot's power switch reachable.

## Running the demo

```bash
.venv/bin/ftd listen --loop
```

* Press **Enter**, say clearly: **"Find the yellow duck."**
* The screen shows what was heard and narrates: *Scanning left… Target seen!…
  Moving toward open floor… Found it!*
* Swap objects between runs: "find the red cup", "find the green bottle".

## Stopping — three ways, all instant and local

| Action | Where |
|---|---|
| Press **Enter** | the terminal running the mission |
| `Ctrl-C` | same terminal |
| `.venv/bin/ftd stop` | any other SSH window |

The physical power switch is the final backstop — software cannot guarantee a
stop if the computer itself freezes.

## If something goes wrong

* **It can't find the object**: better light, a clearer description ("yellow
  rubber duck"), or place the object more in the open. Missions give up on
  their own after ~90 s.
* **It says BLOCKED**: it refused to drive — something is close in front or it
  saw no open floor. Reposition the robot by hand and give the command again.
* **Wi-Fi drops**: the robot stops by itself; reconnect and re-run.
* **No live robot**: run the simulation on any laptop —
  `ftd replay tests/fixtures/replay_center.json "yellow duck"` shows the full
  decision loop with narration.

## Talking points for students

* The robot never "sees" — it sends a photo to an AI model with *typed
  questions* ("Is the duck visible? left/center/right? is the floor clear?")
  and gets back probabilities, which it must double-check before moving.
* Arrival needs two independent kinds of evidence (camera + ultrasound) —
  one sensor alone can lie.
* Every movement command has an expiry time: if the brain hangs, the wheels
  stop. Ask: why is that designed in?
