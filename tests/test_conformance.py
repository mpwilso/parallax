"""conformance/ is Polarizer's, copied byte for byte, and Parallax's chain verifier gives every verdict
its expected.json records.

The folder is the same author's conformance suite for docs/LEDGER-SPEC.md in Polarizer
(https://github.com/mpwilso/polarizer), copied from the commit below with `git show`, every tracked
file and nothing else. It's test data here: Parallax never imports or runs its two scripts. The
hashes below keep the copy identical: a changed, added or missing file fails, and a new copy from
Polarizer updates COPIED_FROM and FILES together. .gitattributes keeps every checkout's bytes as copied.
"""
import hashlib
import json
from pathlib import Path

import pytest

from parallax import chainverify

ROOT = Path(__file__).resolve().parent.parent
FOLDER = ROOT / "conformance"
COPIED_FROM = "700498ad4ffe86470712250d2926683b5d2922a9"  # mpwilso/polarizer, master on 2026-11-07
FILES = {
    "conformance/anchors/checkpoint.key": "be4767cf51363cb8333392d4508c7bbd0a4718a4883f52a6463ddb4bd140e9ac",
    "conformance/anchors/other_chain.jsonl": "74558fdace9a91239efb44e9b62ab519b9da1348ae3dcd7e9a7ae8404288abed",
    "conformance/anchors/rewound.jsonl": "d1ac70920e962383e34505ca1976a6cb67a2e0f749b4fbbe5c36a999774a671c",
    "conformance/anchors/rewritten.jsonl": "3fc39d7dbfe3eba4cb6b1951da74754905f753aca2e5f5c0d27f95522101cd17",
    "conformance/broken/bad_chain_id.jsonl": "6fe8733cae311ab787820b0363f9a9a6c6e8646da2b63fd66cc551802e2a7663",
    "conformance/broken/bad_genesis_prev.jsonl": "8b673c5a95773cb582317877fcbbf2cc04f38d3d1ee4476d8aaea78726055e16",
    "conformance/broken/bad_hash.jsonl": "7dcb80b5ad247f73aa94c8f7452952d7226271081cb5a72afcab00d7a99e1572",
    "conformance/broken/bad_prev.jsonl": "3a12ee4238c824ca9c566b12d767e2c11c7670e5fb6c3870d3975daefbd56012",
    "conformance/broken/bad_seq.jsonl": "6fac1bcc0706b8059398935e85d9cc71e97d3d3868257df7576b85d0ff51fd29",
    "conformance/broken/bad_seq_and_bad_hash.jsonl": "ca3cdd406eff37d2bcb0380ff5e92c5fec37806e2d20b727de7aa298bdb5ec67",
    "conformance/broken/bad_utf8.jsonl": "688758c6fcd5806c4ae4b257cf551ab22a0317a8910300c297c00e94f71fba5f",
    "conformance/broken/big_integer.jsonl": "230a804a804f4664a94f1b05f78bacdb9c2eace723b7760226d8b0b165c8f090",
    "conformance/broken/delete_first_line.jsonl": "5f57e0fd03957d14a4cc85c720f0296922354bb6308a6309487f01864522d387",
    "conformance/broken/delete_middle_line.jsonl": "05c94a7688438c85ec55b9dcfa856aa00c69d6e9be13375875533948c76d19e4",
    "conformance/broken/edit_v0_entry.jsonl": "d459869b446f45b756b4da7a7721105157970405b672555a7dd7b28de66f94f9",
    "conformance/broken/edit_value.jsonl": "16833f8573bfe5786f7646127b01dc13827e5ddfa687b33777e614c5a8ec333e",
    "conformance/broken/extra_key_and_bad_hash.jsonl": "74aa9a38ca5fdde0584a90863bc4e785ad47f439033cd801f4718eb632eb8f07",
    "conformance/broken/extra_top_level_key.jsonl": "59005d4ff83b92bcdeaf96f00d51f4e96ab7f5410507c5a41a4ba5e87c5132f6",
    "conformance/broken/garbage_line.jsonl": "e3f267fe11a3f4eed11e2d7be0b9a60ce251cc92fb5b3e3d569ee4619809dbae",
    "conformance/broken/head_extra_key.head": "5eebc9b55474f9ef7144e110bb6b86c1cb81550929233580a6bbbb17d834748d",
    "conformance/broken/head_extra_key.jsonl": "4ec634e87379cfc1005a1eb727f967ce26f4d6a4e9fc39c219d5a44784b8b1b7",
    "conformance/broken/head_hash_mismatch.head": "47a4c1507a98aee95900dec21187ae9d756f942a0ecebae0a1536ec7980e5a7c",
    "conformance/broken/head_hash_mismatch.jsonl": "4ec634e87379cfc1005a1eb727f967ce26f4d6a4e9fc39c219d5a44784b8b1b7",
    "conformance/broken/head_no_newline.head": "ec96f3a8f0fa7025db6d30d2f66400e1f2c213d16e5c3febc5712f93863c8de4",
    "conformance/broken/head_no_newline.jsonl": "4ec634e87379cfc1005a1eb727f967ce26f4d6a4e9fc39c219d5a44784b8b1b7",
    "conformance/broken/head_not_canonical.head": "bcfab39ccb51bd3c1495680f773a8bf027383cb59eda6301762da71f8af36ad6",
    "conformance/broken/head_not_canonical.jsonl": "4ec634e87379cfc1005a1eb727f967ce26f4d6a4e9fc39c219d5a44784b8b1b7",
    "conformance/broken/head_wrong_chain.head": "17e7c4d79cd6e6d23ee5e1465f0a8e8fd356a4c5ba72d867059a772f387731b5",
    "conformance/broken/head_wrong_chain.jsonl": "4ec634e87379cfc1005a1eb727f967ce26f4d6a4e9fc39c219d5a44784b8b1b7",
    "conformance/broken/holds_edit_decision.jsonl": "e36c1a438deb514ac4fb67ac1b383609d783ac1b1a73cd56523b0ffbcad79d87",
    "conformance/broken/holds_rehashed_decision.head": "0dec00756066054cd400cac61335af8db619be6de55f0d847d655fef2778675c",
    "conformance/broken/holds_rehashed_decision.jsonl": "2d94cbc46d2568b9d103ab46b4d68652f37b1baf9de3247ab15bdbda3b1748c1",
    "conformance/broken/insert_float.jsonl": "81e1a3b706ba7216905b091e1e23b00e9aaf930523c0f68b84fa6f9f6d59c013",
    "conformance/broken/lone_surrogate.jsonl": "2f48a4f3d8f18caece98acb1f6a890802a2bae1c2808ec82e3cc0813ffc9e91c",
    "conformance/broken/missing_key.jsonl": "72dbcd8b64105208da7519be28e8d9eb3b0563cb33cbc93ef8bddc37cd179a39",
    "conformance/broken/mix_v0_v1.jsonl": "71e084659ec97b201758d6edd9252079463420d7b91e0aff8b00ab5b17788e32",
    "conformance/broken/non_ascii_key.jsonl": "98bb7f0a5b886734434c825cb14c735d57821e172bd868d8e78108099c599536",
    "conformance/broken/not_object.jsonl": "7b99da4e0f532bcb54a4da5ccccf3a32d91c042a70a71314219ec9dd8458f71b",
    "conformance/broken/oversize_line.jsonl": "291c8a72c7c5c2db70d3d1a0c6287ddeaa363bbf58aa903bf49642190c03edfa",
    "conformance/broken/pins_edit_approval.jsonl": "05359c159719902fc5d4809d5a9bc5f8b0dc2518c75300c03ea3fd80f2e5eb15",
    "conformance/broken/pins_rehashed_rejection.head": "8d2f6518294836b940f36a4ee6f7c2e458e33ad935932101c6912a3a34fc642a",
    "conformance/broken/pins_rehashed_rejection.jsonl": "907e17a4dd0c734be3c7be7fec72beef4194f9b606a27d518b94268745adec43",
    "conformance/broken/reorder_keys.jsonl": "0a9e80c2723979cf9ec91fa142b3d9b4109dbf28c96178581316bee1fda4828e",
    "conformance/broken/scan_edit_flags.jsonl": "d0fc12cd0272f55d7b96e7974a1cdec415d94f7fdc8be608bda3e57126b466ea",
    "conformance/broken/second_genesis.jsonl": "44db2c737840d66da50b19ba920544b4429343dd7e13d5cf87ba7b5abbbd1896",
    "conformance/broken/swap_lines.jsonl": "8473dab78f17296a1c8c1eec570d5c59cebbae16ff68780c1d3d9e917980fee4",
    "conformance/broken/taint_edit_source.jsonl": "13c7b152740da83e430e7008af1abbe8f3efacf6a243a03628bf3336981eae09",
    "conformance/broken/tampered_line_and_head_mismatch.head": "47a4c1507a98aee95900dec21187ae9d756f942a0ecebae0a1536ec7980e5a7c",
    "conformance/broken/tampered_line_and_head_mismatch.jsonl": "16833f8573bfe5786f7646127b01dc13827e5ddfa687b33777e614c5a8ec333e",
    "conformance/broken/tear_last_line.head": "4b137e7fa64d72bd2c37222452ec168122ddc890c263852fe0e10e690406d342",
    "conformance/broken/tear_last_line.jsonl": "710cb25537df807698f77e34fa039813d9ac079d9fb1e267965aea42e76eda8b",
    "conformance/broken/torn_genesis.jsonl": "17a2bafeb03b59334ff6fabc82de093aead4c8eed734b759fe403e730b8a1386",
    "conformance/broken/torn_genesis_with_head.head": "5aac80e33503b063eba97119b74f10f0723b19f122fcab4d6de6a570e8d932a7",
    "conformance/broken/torn_genesis_with_head.jsonl": "17a2bafeb03b59334ff6fabc82de093aead4c8eed734b759fe403e730b8a1386",
    "conformance/broken/torn_tail_and_truncated.head": "4b137e7fa64d72bd2c37222452ec168122ddc890c263852fe0e10e690406d342",
    "conformance/broken/torn_tail_and_truncated.jsonl": "593be253455abe154bcb233f7fb07894829e7a70ca5c4eacde3db7205c9f2833",
    "conformance/broken/truncated.head": "4b137e7fa64d72bd2c37222452ec168122ddc890c263852fe0e10e690406d342",
    "conformance/broken/truncated.jsonl": "f48b7e3c411d9f98e704c8cdd52fc8c511003bc09d809dddc6b53a3a48ed083f",
    "conformance/broken/v0_then_v1.jsonl": "6e8bdbfc86622020b895edb9f74d61d0a4db90fb2b874b16459e913469d0756f",
    "conformance/broken/v0_torn.jsonl": "45ec54681dcb485eb6b14045d4847e193b866797b64218cfcfa1faa59bf5a899",
    "conformance/broken/v0_unsorted_line.jsonl": "7ffeaeed1dc456b89a4a7b1ab166569cd63e2f2273c32421e496acb2fa0d659e",
    "conformance/broken/v0_with_head.head": "9ba7b8362b5ce46cb9d491fe8e972aef24527a1e6c96b5282bdd5ec8a39829c9",
    "conformance/broken/v0_with_head.jsonl": "84300dadc110758841edbd1fd2b5708fdaa05486b1391e08f7cfe14fc03a73ca",
    "conformance/broken/v_not_1.jsonl": "5aa5a67f67cbe50929765ceda328ceaf066b6087648014a8b8ddaa81d5acfeab",
    "conformance/broken/wrong_type.jsonl": "9935f35e5e6bc37ae418709f72becd253bae1d83d1b4577766a2c66acba3fe1f",
    "conformance/expected.json": "cea13771b923598958be99f6579d7bef24fb48f10f4faa319b5ad9b079529ea5",
    "conformance/reference_anchor.py": "7d65148329862d7bf5ee3b93a9e1a2529d75eaf3a1e9851e57b351c460693a4a",
    "conformance/reference_verify.py": "4b9049d8891a6141eda91ced36fb4a8f14a4e18d05003e463bb927f6c08f2616",
    "conformance/valid/drills.head": "5aac80e33503b063eba97119b74f10f0723b19f122fcab4d6de6a570e8d932a7",
    "conformance/valid/drills.jsonl": "a19c983c9ff050ed6c93442575ccf6263a58cbfea63c4057698a7ce43f14e024",
    "conformance/valid/head_at_last.head": "699e247209aa25d77298e99276e59f0169522731da9c157f79aeff4d2118175b",
    "conformance/valid/head_at_last.jsonl": "4ec634e87379cfc1005a1eb727f967ce26f4d6a4e9fc39c219d5a44784b8b1b7",
    "conformance/valid/holds.head": "0dec00756066054cd400cac61335af8db619be6de55f0d847d655fef2778675c",
    "conformance/valid/holds.jsonl": "381b530df7507b5e2c6d66033bf41e91137f6cb34895616b95716c8707df7e20",
    "conformance/valid/minimal.head": "5aac80e33503b063eba97119b74f10f0723b19f122fcab4d6de6a570e8d932a7",
    "conformance/valid/minimal.jsonl": "e430d1ff392eb97559c839253da8b74d3bbc83e634fbf528dffb83cc4d19dc07",
    "conformance/valid/no_head.jsonl": "4ec634e87379cfc1005a1eb727f967ce26f4d6a4e9fc39c219d5a44784b8b1b7",
    "conformance/valid/pins.head": "8d2f6518294836b940f36a4ee6f7c2e458e33ad935932101c6912a3a34fc642a",
    "conformance/valid/pins.jsonl": "1dbd11761c5eac1bcef321420b1d1dff4886971c1b5901e90e1d6a0493148e3f",
    "conformance/valid/planted.head": "59eed8e93889101f5ca426e26008ccbd39fa435735c60a7a226f3a7f652c925c",
    "conformance/valid/planted.jsonl": "a72761b49409e362d5c7eab029fe86fbce216e46cdc34cb927f2ab5a97088ded",
    "conformance/valid/scan.head": "4c1341b6c0e3e6e9c0e9cfd97fb86bcc9f99ef09df6bdbd995863922b8b75da7",
    "conformance/valid/scan.jsonl": "0aee364fcac38ebf2892bb9915ce1e79769ac1200cca778dbfc0fba20979e080",
    "conformance/valid/session.head": "4b137e7fa64d72bd2c37222452ec168122ddc890c263852fe0e10e690406d342",
    "conformance/valid/session.jsonl": "4ec634e87379cfc1005a1eb727f967ce26f4d6a4e9fc39c219d5a44784b8b1b7",
    "conformance/valid/taint.head": "57355ae9a1b79d783a52fb2488b46ea0213639ac81a4ea72280b026005fe7b56",
    "conformance/valid/taint.jsonl": "433df6cefb686abbbd84dd3f82cfc08ced22982ac81f5bc8a3d70041eb518e8a",
    "conformance/valid/unicode.head": "5aac80e33503b063eba97119b74f10f0723b19f122fcab4d6de6a570e8d932a7",
    "conformance/valid/unicode.jsonl": "3b6949711090226f780ae66778526c6a5d4a15cdb77d87687ae2297635cd3eb2",
    "conformance/valid/v0-parallax.jsonl": "84300dadc110758841edbd1fd2b5708fdaa05486b1391e08f7cfe14fc03a73ca",
}
SECTIONS = {"about", "fixtures", "v0_source", "anchors"}
# what Parallax doesn't do, by name: checkpoints are Polarizer's, signed with a key in its ledger folder
NOT_IMPLEMENTED = {"anchors": "checkpoint anchors (Polarizer's verify --anchor, EXPORT-SPEC.md section 4): "
                              "Parallax doesn't check Polarizer's signed checkpoints"}


def expected() -> dict:
    return json.loads((FOLDER / "expected.json").read_text(encoding="utf-8"))


def test_the_folder_is_the_one_copied_from_polarizer_byte_for_byte():
    found = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in FOLDER.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    assert set(found) == set(FILES), f"added: {sorted(set(found) - set(FILES))}, missing: {sorted(set(FILES) - set(found))}"
    changed = sorted(f for f in FILES if found[f] != FILES[f])
    assert not changed, f"changed since Polarizer {COPIED_FROM[:7]}: {changed}"


@pytest.mark.parametrize("name", sorted(expected()["fixtures"]))
def test_every_fixture_gets_the_verdict_expected_json_records(name):
    want = expected()["fixtures"][name]
    head = FOLDER / f"{name}.head"
    assert head.exists() == want["head"], f"{name}: expected.json says head is {want['head']}"
    verdict = chainverify.verify_bytes((FOLDER / f"{name}.jsonl").read_bytes(), head.read_bytes() if head.exists() else None)
    got = {"status": verdict.status, "check": verdict.check, "line": verdict.line, "seq": verdict.seq}
    assert got == {k: want[k] for k in got}, verdict.text()


def test_every_chain_in_the_folder_has_an_expected_verdict():
    chains = {p.relative_to(FOLDER).as_posix().removesuffix(".jsonl") for p in FOLDER.rglob("*.jsonl")}
    assert chains == set(expected()["fixtures"])


def test_sections_parallax_doesnt_implement_are_named_never_passed_over():
    """A section a later copy adds fails here until it's checked or named in NOT_IMPLEMENTED."""
    assert set(expected()) == SECTIONS
    assert set(NOT_IMPLEMENTED) <= SECTIONS


def test_the_anchor_cases_are_skipped_by_name():
    cases = sorted(expected()["anchors"])
    pytest.skip(f"not implemented in Parallax: expected.json's anchors ({', '.join(cases)}): {NOT_IMPLEMENTED['anchors']}")


def test_the_v0_fixture_came_from_parallaxs_own_ledger():
    source = expected()["v0_source"]
    assert source["repo"] == "parallax" and source["file"] == "parallax/ledger.py"
    assert expected()["fixtures"]["valid/v0-parallax"]["status"] == "intact"
