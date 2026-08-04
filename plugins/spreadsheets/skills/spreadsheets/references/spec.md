# XLSX JSON specification

The `create` command accepts a UTF-8 JSON object:

```json
{
  "title": "Operating model",
  "author": "Example Team",
  "sheets": [
    {
      "name": "Summary",
      "rows": [
        ["Month", "Revenue", "Cost", "Margin"],
        ["Jan", 120000, 80000, "=B2-C2"],
        ["Feb", 132000, 84000, "=B3-C3"]
      ],
      "freezePanes": "A2",
      "autoFilter": "A1:D3",
      "columnWidths": {"A": 14, "B": 16, "C": 16, "D": 16},
      "numberFormats": {"B2:D3": "#,##0"},
      "table": {"name": "SummaryData", "ref": "A1:D3"},
      "charts": [
        {
          "type": "line",
          "title": "Revenue trend",
          "dataRange": "B1:B3",
          "categoriesRange": "A2:A3",
          "anchor": "F2"
        }
      ]
    }
  ]
}
```

Supported sheet fields are `name`, `rows`, `styledHeader`, `freezePanes`, `autoFilter`, `mergedCells`, `columnWidths`, `numberFormats`, `table`, and `charts`. Chart types are `bar`, `line`, and `pie`; chart ranges use A1 notation without a sheet prefix.
