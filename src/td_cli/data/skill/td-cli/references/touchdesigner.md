# Find the theory needed for the current edit

Use Derivative's manual for semantics and installed CLI help for syntax; neither
substitutes for reading the actual graph. Search the exact operator/class on
`derivative.ca/UserGuide/` or `docs.derivative.ca` when its behavior matters.
Some manual pages cover newer builds: td-cli's shipped catalog is validated on
TouchDesigner 2025.32050. A newer manual feature is not proof of local support.

| Question | Official reference and CLI discovery |
| --- | --- |
| When does a graph update? | [Cook](https://derivative.ca/UserGuide/Cook): dependencies drive cooking; cached inspection does not force a cook. Inspect state with `td ops state --help` and family metadata with `td ops inspect --help`. |
| Which operator family/type? | [Operator](https://derivative.ca/UserGuide/Operator): TOP images, CHOP channels, DAT data/text, SOP geometry, COMP components. Use the installed `td ops types --help`, then inspect candidate types and actual input/output connectors. |
| Wires versus references? | Regular wires carry family data; COMP hierarchy wires express parent/child relationships. A parameter reference is a separate dependency. Use `td ops connections --help` or `td ops hierarchy --help` for the corresponding relationship. |
| Why did a parameter not behave like a number? | [Parameter Mode](https://derivative.ca/UserGuide/Parameter_Mode): constant, expression, export and bind differ. `td parameters list --help` and `td parameters get --help` exposes actual types and modes. Expression reads return source text, not evaluated CHOP values. Export requires an existing matching source. |
| What is a live CHOP value? | [CHOP to DAT](https://derivative.ca/UserGuide/CHOP_to_DAT): use a temporary CHOP to DAT with the intended channel/time scope, then bounded `td dat table get --help`. Remove only the inspection node you created. Cached family metadata is not sample data. |
| Why no connection? | [SocketIO DAT](https://derivative.ca/UserGuide/SocketIO_DAT): networking and connection callbacks differ from timeline execution. Read the Agent state and daemon logs; timeline pause alone is not a reason to restart TD. |
| What does saving mean? | [Project Class](https://derivative.ca/UserGuide/Project_Class): save writes the current session; quit is a separate operation. Follow the CLI's current-path/digest contract rather than calling arbitrary Python. |

For OSC, map actual received channel names and units before designing effects.
Use [OSC In CHOP](https://derivative.ca/UserGuide/OSC_In_CHOP) or the relevant DAT
manual as appropriate. A Select CHOP selects channels; avoid accidentally
recomputing the user's public signals while wiring consumers.

Prefer the existing supported typed commands. When a necessary operation is
missing, report its narrow expected behavior and a reproducible limitation;
do not embed arbitrary Python into another command to bypass the interface.
