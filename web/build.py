"""Build the Claude artifact page: web/digest.template.html + sample data -> web/puckdesk-digest.html."""

import json
from pathlib import Path

here = Path(__file__).parent
template = (here / "digest.template.html").read_text()
sample = json.loads((here / "sample-digest.json").read_text())
blob = json.dumps(sample, ensure_ascii=False).replace("</", "<\\/")
(here / "puckdesk-digest.html").write_text(template.replace("__SAMPLE__", blob))
print("wrote web/puckdesk-digest.html")
