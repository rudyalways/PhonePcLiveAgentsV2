---
name: macos-tools
description: "macOS native integrations: screen capture, calendar, reminders, contacts, email (Mail.app), Spotlight search. Use when the user asks about their screen, schedule, to-do list, contacts, or wants to send email on macOS."
---

# macOS Tools

Native macOS integrations via AppleScript. No API keys needed — works on any Mac.

## When to Use

- **Screen**: "What's on my screen?", "help me with this", "describe what I'm looking at"
- **Calendar**: "What's on my schedule?", "do I have meetings today?"
- **Reminders**: "Add a reminder", "what's on my todo list?", "mark X as done"
- **Contacts**: "What's Bob's email?", "find contact for..."
- **Email**: "Send an email to...", "draft a message to..."
- **File search**: "Find my resume", "where's that PDF?"

## Screen Tool Preference

For screen-related requests, prefer local macOS visibility before browser automation:

1. If the task is to inspect or describe what is visible on the Mac, use this skill's screen capture. It calls `/usr/sbin/screencapture` via `screen-capture.sh`, then reads the PNG with vision.
2. If the task needs interacting with a native Mac app or reading structured UI controls, use the `macos-use` MCP accessibility tree.
3. If a built-in "describe screen" or current-screen context is available in the host environment, use that for quick visual understanding.
4. For logged-in websites, prefer the user's existing visible browser session via `macos-use` or screenshot/vision. Do not switch to Playwright, Chromium, or another browser profile if it would require a separate login, lose cookies, or change the user's session context.
5. Use Playwright, Chromium, or browser MCP only when the user explicitly wants browser automation/debugging, or when the task can run without extra login/session setup.

## Tools

### Screen Capture
```bash
bash "$SKILL_DIR/scripts/screen-capture.sh"
```
Returns path to PNG screenshot. Use the Read tool on the path to view it.

### Calendar
Prefer the `google-calendar` skill if installed. Fallback to macOS Calendar:
```bash
python3 "$SKILL_DIR/scripts/calendar-reader.py" 7          # next 7 days, JSON
python3 "$SKILL_DIR/scripts/calendar-reader.py" 1 text     # today, plain text
```

### Reminders
```bash
python3 "$SKILL_DIR/scripts/reminders.py" list              # all incomplete
python3 "$SKILL_DIR/scripts/reminders.py" add "Call Bob"     # add reminder
python3 "$SKILL_DIR/scripts/reminders.py" add "Fix bug" "2026-03-17"  # with due date
python3 "$SKILL_DIR/scripts/reminders.py" complete "Call Bob" # mark done
```

### Contacts
```bash
python3 "$SKILL_DIR/scripts/contacts.py" search "Bob"       # find by name
```
Returns name, emails, phones. Use before sending email to resolve names to addresses.

### Email (Apple Mail)
```bash
python3 "$SKILL_DIR/scripts/email-sender.py" "to@example.com" "Subject" "Body"
python3 "$SKILL_DIR/scripts/email-sender.py" "to@example.com" "Subject" "Body" --draft
```
Sends via Mail.app. Use `--draft` to create without sending. **Always confirm with user before sending.**

### Spotlight File Search
```bash
mdfind "quarterly report"                    # search by content or filename
mdfind -name "resume.pdf"                    # search by filename only
```

## Requirements

- macOS (uses AppleScript)
- Calendar, Reminders, Contacts, Mail apps (built into macOS)
- Grant Accessibility permissions if prompted
