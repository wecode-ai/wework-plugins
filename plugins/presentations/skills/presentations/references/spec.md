# PPTX JSON specification

The `create` command accepts a UTF-8 JSON object with an optional theme and a non-empty slide list:

```json
{
  "theme": {
    "background": "F8FAFC",
    "foreground": "0F172A",
    "muted": "475569",
    "accent": "2563EB"
  },
  "slides": [
    {
      "layout": "title",
      "title": "Operating review",
      "subtitle": "A decision-focused update"
    },
    {
      "layout": "content",
      "title": "What changed",
      "bullets": [
        "Demand grew in the enterprise segment",
        {"text": "Retention improved after onboarding changes", "level": 0}
      ]
    },
    {
      "layout": "two-column",
      "title": "Options",
      "columns": [
        {"heading": "Invest", "bullets": ["Faster growth", "Higher near-term cost"]},
        {"heading": "Hold", "bullets": ["Lower risk", "Slower learning"]}
      ]
    },
    {"layout": "section", "title": "Recommendation", "subtitle": "Fund the measured expansion"}
  ]
}
```

Colors are six-digit RGB strings. Supported layouts are `title`, `content`, `two-column`, and `section`. Bullet entries may be strings or objects with `text` and `level`.
