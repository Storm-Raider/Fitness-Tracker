
## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool. When in doubt, invoke the skill.

Key routing rules:
- Product ideas/brainstorming → invoke /office-hours
- Strategy/scope → invoke /plan-ceo-review
- Architecture → invoke /plan-eng-review
- Design system/plan review → invoke /design-consultation or /plan-design-review
- Full review pipeline → invoke /autoplan
- Bugs/errors → invoke /investigate
- QA/testing site behavior → invoke /qa or /qa-only
- Code review/diff check → invoke /review
- Visual polish → invoke /design-review
- Ship/deploy/PR → invoke /ship or /land-and-deploy
- Save progress → invoke /context-save
- Resume context → invoke /context-restore

## Design System

Always read DESIGN.md before making any visual or UI decisions.
Font choices, colors, spacing, aesthetic direction, and design principles are defined there.
Do not deviate without explicit user approval.
Key rules:
- Blue (`--accent`) = interactive chrome only. Gold (`--pr`) = earned achievement only. Never swap.
- No light mode — this is a documented position, not a missing feature.
- All numeric data uses JetBrains Mono via the `.num` class.
- Touch targets: primary actions ≥56px height, steppers ≥40px.
