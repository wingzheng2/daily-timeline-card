# Public palette presets

Use the single public preset or a user-confirmed custom palette. Keep category meaning independent from color.

| Preset | Background | Ink | 睡眠/生活 | 学习/找工作 | 饮食 | 娱乐社交 |
|---|---|---|---|---|---|---|
| 雾蓝日常 | `#F6F8F9` | `#30404C` | `#D8DEE3` | `#A7BFCC` | `#B8B4CE` | `#D7B2A5` |

Do not offer or reproduce the private skill's named presets.

## Custom palette checks

- Require exactly four category fill colors plus a background and ink color.
- Accept ordinary color descriptions or hex values; normalize the final selection to hex.
- Avoid nearly identical fills for adjacent categories.
- Prefer medium-light, low-saturation fills so dark text remains readable.
- If a requested color has weak contrast with the ink, explain the issue and propose the nearest safer shade before rendering.
- Show `分类 → 色值` for all four fixed categories and obtain confirmation.
