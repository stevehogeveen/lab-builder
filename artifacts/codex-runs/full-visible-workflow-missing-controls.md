# Full Visible Workflow Missing Controls

Total rows: 0

| module | route | method | label | action | inputs | location | purpose |
| --- | --- | --- | --- | --- | --- | --- | --- |

All currently known dynamic visible actions have React equivalents:
- Dashboard module rows now render React-aware links from each backend `legacy_href`, including the current next-step marker.
- Run Center stage rows now render `review_href` and blocked-stage `fix_href` controls from `execution_review.stages`.
