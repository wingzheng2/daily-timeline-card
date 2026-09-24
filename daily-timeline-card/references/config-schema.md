# Public renderer configuration

The renderer accepts UTF-8 JSON. Use 24-hour `HH:MM` times and list events chronologically from the first boundary. An end time that is not later than its normalized start is placed on the next day.

## Required shape

```json
{
  "date": "09.20",
  "ratio": "3:4",
  "classification_confirmed": true,
  "palette_confirmed": true,
  "require_24_hours": true,
  "background": "#F6F8F9",
  "ink": "#30404C",
  "categories": [
    {"id": "sleep-life", "label": "睡眠/生活", "color": "#D8DEE3"},
    {"id": "study-job", "label": "学习/找工作", "color": "#A7BFCC"},
    {"id": "meals", "label": "饮食", "color": "#B8B4CE"},
    {"id": "leisure-social", "label": "娱乐社交", "color": "#D7B2A5"}
  ],
  "events": [
    {"start": "00:00", "end": "08:00", "label": "睡觉", "category": "sleep-life"},
    {"start": "08:00", "end": "08:30", "label": "吃早饭", "category": "meals"},
    {"start": "08:30", "end": "12:00", "label": "上课", "category": "study-job"},
    {"start": "12:00", "end": "13:00", "label": "吃午饭", "category": "meals"},
    {"start": "13:00", "end": "18:00", "label": "学习/找工作", "category": "study-job"},
    {"start": "18:00", "end": "19:00", "label": "吃晚饭", "category": "meals"},
    {"start": "19:00", "end": "22:30", "label": "娱乐", "category": "leisure-social"},
    {"start": "22:30", "end": "24:00", "label": "洗漱、休息", "category": "sleep-life"}
  ]
}
```

## Allowed values

- `date`: use either `MM.DD` or an English date such as `September 20`; never include `DAY N`.
- `ratio`: allow only `3:4` or `1:1`; default to `3:4`.
- `categories`: use exactly the four category objects above, preserving IDs and labels. Replace only their colors when the user selects another confirmed palette.
- `next_day_prefix`: optional; default to `次日`.

Do not include `day_label`, `font_regular`, `font_bold`, or `9:16` in a public-version config.

## Invariants

- Use the exact category IDs `sleep-life`, `study-job`, `meals`, and `leisure-social` once each.
- Make every event category reference one of those IDs.
- Make adjacent events touch exactly: each next start equals the previous end after normalization.
- Reject zero-duration events.
- When `require_24_hours` is `true`, require the first boundary to final boundary to total 1440 minutes.
- Derive legend totals from events; never supply them manually.
- Block rendering unless both confirmation flags are `true`.

## Font behavior

Use the embedded Noto Sans CJK SC Regular and Bold data modules as one consistent public type system. Do not accept user-supplied font overrides. The renderer materializes the fonts temporarily at runtime, so the upload contains only platform-supported source-file types. If either font data module is missing, PNG rendering stops with a clear error; SVG remains usable because the viewing system can supply a CJK font.
