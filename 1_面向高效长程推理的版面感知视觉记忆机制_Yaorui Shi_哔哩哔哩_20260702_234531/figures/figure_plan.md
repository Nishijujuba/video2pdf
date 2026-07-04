# Figure Plan

Source contact sheet: `figures/contact_sheet_30s.jpg`. Final selected figures are cropped from `figures/candidates_range/frame_*.jpg`, using the verified Range-downloaded video `source/video_480_range.mp4`.

| Figure | File | Source frame | Time interval | Insert near | Purpose |
|---|---|---|---|---|---|
| 1 | `figures/selected/fig01_agentic_memory_background.png` | `frame_0006.jpg` | 00:02:30--00:03:00 | section_01 | Compare raw history memory, textual summary memory, and visual memory; establish why visual layout changes compression behavior. |
| 2 | `figures/selected/fig02_textual_memory_baselines.png` | `frame_0012.jpg` | 00:05:30--00:06:00 | section_02 | Show MemAgent and Mem-a as textual-memory baselines before introducing the paradigm shift. |
| 3 | `figures/selected/fig03_text_to_vision_domain.png` | `frame_0015.jpg` | 00:07:00--00:07:30 | section_02 | Explain the transition from text-domain memory drafting to vision-domain memory reading. |
| 4 | `figures/selected/fig04_budget_aware_training.png` | `frame_0018.jpg` | 00:08:30--00:09:00 | section_03 | Show budget-aware objectives and the evidence-level / memory-budget task grid. |
| 5 | `figures/selected/fig05_memory_budget_results.png` | `frame_0026.jpg` | 00:12:30--00:13:00 | section_04 | Summarize main experimental result: MemOCR retains performance under limited memory budget. |
| 5b | `figures/selected/frame_0032_layout_efficiency_density_emergence.png` | `frame_0032.jpg` | 00:15:30--00:16:00 | section_04 | Diagnose why the main result holds: oracle evidence in the crucial area survives compression better than evidence in the detail area. |
| 6 | `figures/selected/fig06_layout_efficiency_density.png` | `frame_0032.jpg` | 00:15:30--00:16:00 | section_05 | Show layout efficiency and adaptive density: crucial regions receive more useful evidence. |
| 7 | `figures/selected/fig07_case_study_layout_control.png` | `frame_0034.jpg` | 00:16:30--00:17:00 | section_05 | Case study comparing truncation, visual memory without layout control, and MemOCR layout control. |
| 8 | `figures/selected/fig08_complexity_analysis.png` | `frame_0036.jpg` | 00:17:30--00:18:00 | section_06 | Explain runtime cost and why compression can become more attractive as context grows. |
| 9 | `figures/selected/fig09_failure_analysis.png` | `frame_0040.jpg` | 00:19:30--00:20:00 | section_07 | Show two failure modes: fine-grained comparative details lost, and memory capacity overflow. |

Quality notes:

- The first `yt-dlp` downloads produced corrupt same-size media because interrupted range resume assembled invalid NAL units. The usable video source is `source/video_480_range.mp4`, produced by verified fixed-range downloading.
- Crops remove the speaker and QR code area. Since the source is 480P, small table values are for visual context; exact numeric claims should rely on subtitle narration unless the number is legible in the crop.
- Figure footnotes in TeX should use the concrete time interval above.
