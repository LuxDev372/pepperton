"""Static contract for the stock observatory's social extension."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGE = (ROOT / "server/static/index.html").read_text()


def check(label, condition):
    if not condition:
        raise AssertionError(label)
    print(f"PASS {label}")


for marker in (
    'id="socialBtn"', 'id="socialPanel"', 'id="socialScenes"',
    'id="socialCommitments"', 'id="socialHistory"', 'id="socialMetrics"',
    'id="socialFields"', 'id="socialProject"', 'id="socialHours"',
    'id="socialGo"', 'interact:{ social: true }',
):
    check(f"stock UI exposes {marker}", marker in PAGE)

check("snapshot renders the public social projection", "renderSocial(snap.social);" in PAGE)
check("social action posts through the existing possession boundary",
      'const { ok, data } = await seatPost(act);' in PAGE and
      'const act = { action: "interact", act: actName };' in PAGE)
check("stock UI submits typed project-completion commitments",
      'kind: "project_complete"' in PAGE and
      'condition: "contribute " + Number(hours)' in PAGE and
      'choose a project and hours together' in PAGE)
check("acceptance uses the authoritative commitment field",
      'if (actName === "accept") act.commitment = commitment;' in PAGE)
check("stock UI no longer accepts free-form commitment prose",
      'id="socialCommitment"' not in PAGE)

render_social = PAGE.split("function renderSocial", 1)[1].split("function appendEvents", 1)[0] \
    if "function appendEvents" in PAGE.split("function renderSocial", 1)[1] else PAGE.split("function renderSocial", 1)[1].split("feed.addEventListener", 1)[0]
check("social drawer does not render private proposals or proof",
      "proposal" not in render_social and "proof" not in render_social and "condition" not in render_social)
check("stock UI does not include the redesign's illustrated-map code",
      "drawIllustratedTown" not in PAGE and "illustratedMaps" not in PAGE and "assets/" not in PAGE)

print("Stock social UI contract complete")
