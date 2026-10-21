# Product Definition & Core User Journeys

**Product:** IKEA Flatpack Co-Pilot  
**Hackathon:** Amazon Alexa+ / Fire TV Developer Hackathon  
**Goal:** Highly interactive, autonomous AI assistant for assembling modern IKEA furniture (e.g. VATTENKAR, RISATORP).

## Constraints / demo requirements

- Utilize the **2025-11-25 Streamable HTTP MCP** spec (after HTTP validation)
- Cross-service autonomous orchestration
- Physical-world state tracking
- Purchasing flows (Amazon / Stripe via tools)

## Role of computer vision

The CV pipeline translates tabletop chaos into structured data that maps to IKEA’s wordless visual diagrams. The model acts as an **active spatial reasoning engine**, not a passive camera feed.

---

## Journey 1 — Hardware identification (“Which screw is this?”)

**Context:** Manuals identify hardware with 6-digit part numbers (e.g. `#100001` Allen key, `#114794` hex bolt).

| | |
|---|---|
| **User** | Holds a screw to the camera (or places it center-desk) and asks: “Is this for Step 2?” |
| **CV** | Object detection + relative size estimation (thread pitch, head type, length) |
| **Output** | UI highlights the 6-digit code on the digital manual; Alexa: “Yes, that’s hex bolt 114794. You need two of them for this step.” |

---

## Journey 2 — Spatial orientation (“Did I put this on backwards?”)

**Context:** Prevents assembly disasters from flipped / mirrored parts.

| | |
|---|---|
| **User** | Connects a side bracket, pauses, or asks: “Does this look right?” |
| **CV** | Keypoint / asymmetry checks (pre-drilled holes, grooves) vs expected diagram |
| **Output** | Red overlay on misaligned features; Alexa interrupts to advise flipping |

---

## Journey 3 — Inventory & state (“Am I missing parts?”)

| | |
|---|---|
| **User** | Dumps hardware bag on desk: “Scan my parts.” |
| **CV** | Multi-object detection + counting vs bill of materials |
| **Output** | Alexa: “You have all 6 hex bolts, but you’re missing one thumb screw. Want me to order a replacement?” → purchasing flow |

---

## Journey 4 — Automatic step progression (“Next”)

| | |
|---|---|
| **User** | Finishes Step 3, sets sub-assembly down (no “Next” required) |
| **CV** | Physical shape matches Step 3 completion illustration |
| **Output** | Digital manual advances to Step 4 on Fire TV / browser |

---

## Runtime assembly state

The system always maintains an `AssemblyState`:

```ts
interface AssemblyState {
  productId: string;
  currentStep: number;
  requiredHardware: { partId: string; qty: number }[];
  stepCompleted: boolean;
  activeMode:
    | "IDLE_MONITORING"
    | "COUNTING"
    | "INSPECTING_PART"
    | "INSPECTING_ORIENTATION";
}
```
