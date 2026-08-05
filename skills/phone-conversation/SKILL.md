---
name: phone-conversation
description: "Make conversational phone calls and join Zoom meetings via Twilio + Gemini. Multi-turn AI conversations on the phone on behalf of the user."
---

# Phone Conversation

Make outbound phone calls and join Zoom meetings where Sutando has a real multi-turn conversation, powered by Gemini.

## When to Use

- "Call +14155551234 and ask if they're available for dinner"
- "Call the restaurant and make a reservation for 7pm"
- "Call my dentist and reschedule my appointment"
- "Phone the landlord and ask about the maintenance request"
- "Join my Zoom meeting 1234567890"
- "Dial into the meeting and take notes"
- Any time you need Sutando to have a phone conversation or join a meeting on your behalf

## How It Works

Uses Twilio Media Streams for real-time bidirectional audio, piped to Gemini Live for natural conversation. The caller can interrupt mid-sentence — no waiting for the AI to finish speaking.

1. Call connects → Twilio opens a WebSocket audio stream
2. Audio flows bidirectionally between the caller and Gemini Live
3. Gemini responds in real time, interruptible at any point
4. Full transcript is saved when the call ends
