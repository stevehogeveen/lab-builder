# Full Visible Exact Label Follow-up

Status: no backend route gaps found.

Route scan:
- Original route decorators found: 122
- React route decorators found: 144
- Original routes missing from React: 0
- React-only routes are the `/api/ui/*` API surface plus `/react-preview`.

Remaining exact-label differences are not currently classified as missing functionality:
- Generic `Open log`, `Open bundle`, `View`, and `Download` controls are exposed through the React Reports, Debug Mode, and action inventory surfaces.
- Storage/history artifact labels such as `View Apply Log`, `View raw discovery`, `Open run summary`, and `Open storage plan used` are now explicit React action inventory entries.
- Dynamic storage approval/reboot Jinja labels resolve to React controls for `Approve this plan`, `Reboot Machine Now`, and `Reboot Now`.
- `Setup Cisco IP` remains intentionally explicit in React because the operator workflow needs a clear setup-IP button before Run Center execution.
- Context-heavy forms still open the original full form instead of submitting incomplete React-side POSTs.

Operator wording cleanup:
- Run Center Cisco readiness now points operators to `Setup Cisco IP`, matching the visible React action.

Latest validation:
- Focused visible/UI parity contracts: 33 passed.
- Full suite: 457 passed in 242.63s.
