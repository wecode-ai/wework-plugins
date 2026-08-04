# PDF JSON specification

The `create` command accepts a UTF-8 JSON object:

```json
{
  "title": "Research brief",
  "author": "Example Team",
  "subject": "Market analysis",
  "pageSize": "A4",
  "marginInches": 0.75,
  "content": [
    {"heading": "Summary"},
    {"paragraph": "This PDF is created from a structured, editable source specification."},
    {"bullets": ["Evidence first", "Concise conclusions"]},
    {
      "table": {
        "columns": ["Scenario", "Impact"],
        "rows": [["Base", "Moderate"], ["Upside", "High"]]
      }
    },
    {"image": "/absolute/path/chart.png", "widthInches": 5.5, "caption": "Figure 1"},
    {"pageBreak": true},
    {"heading": "Appendix"}
  ]
}
```

`pageSize` supports `A4` and `LETTER`. Text styles select a local Unicode font suitable for common Chinese and Latin text. Supported content objects are `heading`, `paragraph`, `bullets`, `table`, `image`, and `pageBreak`.

The CLI embeds a compatible local Unicode font when one is available. Set `WEGENT_ARTIFACT_FONT` to an absolute `.ttf` or supported `.ttc` path to choose a specific font; otherwise it checks common macOS, Linux, and Windows font locations before using a PDF CID-font fallback.
