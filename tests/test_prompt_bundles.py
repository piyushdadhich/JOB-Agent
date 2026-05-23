"""End-to-end tests for the Phase 3 prompt bundle generator.

Run from project root with venv active:
    venv\\Scripts\\python.exe tests\\test_prompt_bundles.py

Uses a dedicated test database and a temporary applications root that
are cleaned up at the end. Exits 0 on full pass, 1 otherwise.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.persistence.tracker import Tracker  # noqa: E402
from agents.resume_loader import load_resume_as_markdown  # noqa: E402
from agents.inventory_loader import (  # noqa: E402
    load_inventory, filter_inventory_for_opportunity,
)
from agents.prompt_bundles import PromptBundleGenerator  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_DB_PATH = PROJECT_ROOT / "data" / "test_prompt_bundles.db"
SCHEMA_PATH = PROJECT_ROOT / "data" / "schemas" / "v2" / "schema.sql"

VARIANTS = ("public_sector", "financial_services",
            "healthcare_education", "real_estate")

results: list[tuple[str, bool, str]] = []


def record(name: str, passed: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}  {detail}")
    results.append((name, passed, detail))


def init_fresh_db() -> None:
    import sqlite3
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()
    conn = sqlite3.connect(str(TEST_DB_PATH))
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def main() -> int:
    print(f"Using test db: {TEST_DB_PATH}\n")
    init_fresh_db()
    tmp_apps = Path(tempfile.mkdtemp(prefix="apps_test_"))
    tracker = Tracker(profile_id="test_pb", db_path=str(TEST_DB_PATH))

    try:
        # 1. resume_loader: load each variant
        print("Test 1: resume_loader loads all four variants")
        ok = True
        per_variant = {}
        for v in VARIANTS:
            try:
                txt = load_resume_as_markdown(v)
                per_variant[v] = len(txt)
                if not txt or len(txt) < 50:
                    ok = False
            except Exception as e:
                ok = False
                per_variant[v] = f"err:{e}"
        record("resume_loader_all_variants", ok, str(per_variant))

        # 2. resume_loader: invalid variant -> ValueError
        print("\nTest 2: resume_loader invalid variant raises ValueError")
        try:
            load_resume_as_markdown("not_a_variant")
            record("resume_loader_invalid", False, "no exception")
        except ValueError:
            record("resume_loader_invalid", True, "ValueError raised")
        except Exception as e:
            record("resume_loader_invalid", False,
                   f"wrong exception: {type(e).__name__}: {e}")

        # 3. inventory_loader: load returns structure with at least one role
        print("\nTest 3: inventory_loader.load_inventory")
        inv = None
        try:
            inv = load_inventory()
            roles = inv.get("roles", [])
            ok = (isinstance(inv, dict) and len(roles) >= 1
                  and "title" in roles[0])
            record("inventory_load", ok, f"roles={len(roles)}")
        except Exception as e:
            record("inventory_load", False, f"exception: {e}")

        # 4. inventory filter for sample opportunity
        print("\nTest 4: filter_inventory_for_opportunity")
        try:
            sample_opp = {
                "title": "Senior Delivery Lead",
                "employer": "Discover",
                "fit_reasoning": "credit decisioning java spring boot",
                "notes": "",
            }
            filt = filter_inventory_for_opportunity(inv, sample_opp, top_n=3)
            ok = ("all_role_summaries" in filt
                  and "detailed_roles" in filt
                  and len(filt["detailed_roles"]) >= 1
                  and len(filt["detailed_roles"]) <= 3)
            record("inventory_filter", ok,
                   f"detail={len(filt['detailed_roles'])} "
                   f"summaries={len(filt['all_role_summaries'])}")
        except Exception as e:
            record("inventory_filter", False, f"exception: {e}")

        # 5. PromptBundleGenerator: cover letter bundle
        print("\nTest 5: generate cover_letter bundle")
        gen = PromptBundleGenerator(tracker=tracker, applications_root=tmp_apps)
        cover_app_id = None
        try:
            company_id = tracker.upsert_company(name="Acme Bank")
            opp_id, _ = tracker.insert_opportunity(
                company_id=company_id,
                source="linkedin",
                source_url="https://example.com/jobs/cover1",
                title="Senior Delivery Lead",
            )
            tracker.record_evaluation(
                opportunity_id=opp_id,
                evaluator_version="legacy",
                tier="EXPLORATORY",
                fit_score=8,
                sector="financial_services",
                role_type=None,
                stage_trace={"source": "test_prompt_bundles"},
                reasoning="java spring boot delivery lead",
            )
            res = gen.generate_bundle(opp_id, bundle_type="cover_letter")
            cover_app_id = res["application_id"]
            path = Path(res["bundle_path"])
            content = path.read_text(encoding="utf-8")
            ok = (path.exists()
                  and "## Job Posting" in content
                  and "## Career Inventory" in content
                  and "## Resume Variant" in content
                  and "BEGIN COVER LETTER OPTION A" in content
                  and res["bundle_type"] == "cover_letter"
                  and res["resume_variant"] == "financial_services")
            record("generate_cover_letter", ok, f"path={path.name}")
        except Exception as e:
            record("generate_cover_letter", False, f"exception: {e}")

        # 6. resume_variant_override
        print("\nTest 6: resume_variant_override is honored")
        try:
            company_id2 = tracker.upsert_company(name="Acme Health")
            opp_id2, _ = tracker.insert_opportunity(
                company_id=company_id2,
                source="linkedin",
                source_url="https://example.com/jobs/override1",
                title="Program Manager",
            )
            tracker.record_evaluation(
                opportunity_id=opp_id2,
                evaluator_version="legacy",
                tier="EXPLORATORY",
                fit_score=None,
                sector="financial_services",
                role_type=None,
                stage_trace={"source": "test_prompt_bundles"},
                reasoning=None,
            )
            res = gen.generate_bundle(
                opp_id2, bundle_type="cover_letter",
                resume_variant_override="healthcare_education",
            )
            ok = res["resume_variant"] == "healthcare_education"
            record("resume_variant_override", ok,
                   f"variant={res['resume_variant']}")
        except Exception as e:
            record("resume_variant_override", False, f"exception: {e}")

        # 7. auto-determine bundle type from sector (public_sector)
        print("\nTest 7: auto bundle_type from public_sector")
        public_app_id = None
        opp_id3 = None
        try:
            company_id3 = tracker.upsert_company(name="CRA")
            opp_id3, _ = tracker.insert_opportunity(
                company_id=company_id3,
                source="gc_jobs",
                source_url="https://example.gc.ca/jobs/auto1",
                title="Policy Analyst",
            )
            tracker.record_evaluation(
                opportunity_id=opp_id3,
                evaluator_version="legacy",
                tier="EXPLORATORY",
                fit_score=None,
                sector="public_sector",
                role_type=None,
                stage_trace={"source": "test_prompt_bundles"},
                reasoning=None,
            )
            res = gen.generate_bundle(opp_id3)
            public_app_id = res["application_id"]
            ok = (res["bundle_type"] == "cover_letter_with_federal_responses"
                  and res["resume_variant"] == "public_sector")
            record("auto_bundle_type_public", ok,
                   f"type={res['bundle_type']} variant={res['resume_variant']}")
        except Exception as e:
            record("auto_bundle_type_public", False, f"exception: {e}")

        # 8. application record fields are correct
        print("\nTest 8: application record created with correct fields")
        try:
            app = tracker.get_application_by_id(public_app_id)
            ok = (app is not None
                  and app["opportunity_id"] == opp_id3
                  and app["resume_variant"] == "public_sector"
                  and app["status"] == "drafted")
            record("application_record_fields", ok,
                   f"app_id={app['id']} status={app['status']}")
        except Exception as e:
            record("application_record_fields", False, f"exception: {e}")

        # 9. opportunity marked pursued
        print("\nTest 9: opportunity marked 'pursued' after generation")
        try:
            opp_after = tracker.get_opportunity_by_id(opp_id3)
            ok = opp_after["status"] == "pursued"
            record("opportunity_pursued", ok, f"status={opp_after['status']}")
        except Exception as e:
            record("opportunity_pursued", False, f"exception: {e}")

        # 10. _slugify
        print("\nTest 10: _slugify edge cases")
        try:
            cases = {
                "Hello World": "hello_world",
                "  Foo --- Bar!!!  ": "foo_bar",
                "": "unknown",
                "A" * 100: "a" * 50,
                "Caf\u00e9 / R\u00e9sum\u00e9": None,
            }
            ok = True
            outs = {}
            for inp, expected in cases.items():
                out = gen._slugify(inp)
                outs[repr(inp)[:20]] = out
                if expected is not None and out != expected:
                    ok = False
                if not out or len(out) > 50:
                    ok = False
            record("slugify", ok, str(outs))
        except Exception as e:
            record("slugify", False, f"exception: {e}")

    finally:
        tracker.close()
        try:
            if TEST_DB_PATH.exists():
                os.remove(TEST_DB_PATH)
                print(f"\nCleaned up {TEST_DB_PATH}")
        except OSError as e:
            print(f"\nWARNING: could not delete test db: {e}")
        try:
            shutil.rmtree(tmp_apps, ignore_errors=True)
        except Exception:
            pass

    print("\n" + "=" * 60)
    total = len(results)
    passed = sum(1 for _, p, _ in results if p)
    print(f"{passed}/{total} tests passed")
    for name, p, detail in results:
        print(f"  {'PASS' if p else 'FAIL'}: {name}  {detail}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
