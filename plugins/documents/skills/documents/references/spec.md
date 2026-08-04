# DOCX JSON specification

The `create` command accepts a UTF-8 JSON object:

```json
{
  "title": "Quarterly review",
  "author": "Example Team",
  "subject": "Business update",
  "font": "Aptos",
  "fontSize": 11,
  "marginsInches": {"top": 0.8, "right": 0.9, "bottom": 0.8, "left": 0.9},
  "content": [
    {"heading": "Executive summary", "level": 1},
    {"paragraph": "The quarter closed ahead of plan.", "alignment": "justify"},
    {"bullets": ["Revenue grew", "Retention improved"]},
    {"numbered": ["Confirm scope", "Review evidence", "Publish"]},
    {
      "table": {
        "columns": ["Metric", "Actual", "Target"],
        "rows": [["Revenue", 125, 120], ["Retention", "94%", "92%"]]
      }
    },
    {"image": "/absolute/path/chart.png", "widthInches": 5.8},
    {"pageBreak": true},
    {"heading": "Appendix", "level": 1}
  ]
}
```

Supported content objects are `heading`, `paragraph`, `bullets`, `numbered`, `table`, `image`, and `pageBreak`. Image paths must be readable local files. Heading levels must be valid Word heading levels.
