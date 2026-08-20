# AFL Analytics Agent — Final Version

## Model

This project uses:

`gemini-3.5-flash-lite`

The agent uses the Google `google-genai` SDK directly so Gemini
3.x function-call thought signatures are preserved.

## Features

- AFL player statistics
- Player leaderboards
- Player summaries
- Team summaries
- Match lookup
- Team head-to-head analysis
- Gemini function calling
- Conversation history
- Data-quality validation
- Automated tests

## Colab

Install dependencies:

```python
!pip install -q -r requirements.txt
```