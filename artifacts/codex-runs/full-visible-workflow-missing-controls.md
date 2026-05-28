# Full Visible Workflow Missing Controls

Total rows: 2

| module | route | method | label | action | inputs | location | purpose |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Dashboard / home | /dashboard | GET | {{ loop.index }} {{ item.name }} {{ item.summary }} {% if is_next_step %} Next {% endif %} {{ item.label }} | {{ item.href }} |  | templates/partials/pages/dashboard.html | Open original linked workflow or artifact |
| Execution / Run Center | /execution | GET | {{ stage.fix_label }} | {{ stage.fix_href }} |  | templates/partials/pages/execution.html | Open original linked workflow or artifact |
