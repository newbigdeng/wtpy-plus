"""Contract instances preserve total budgets and applied/draft distinctions."""
from copy import deepcopy
import unittest

from wtpy.monitor.StrategyContracts import contract_codes, contract_plan


CATALOG = [dict(code=code, exchange="SHFE", product=product) for code, product in
           (("SHFE.rb.2701", "rb"), ("SHFE.rb.2705", "rb"), ("SHFE.rb.2710", "rb"), ("SHFE.cu.2701", "cu"))]


def plan():
    return dict(revision="existing", strategies=[dict(id="trend", name="合约管理示例", class_name="ExampleCta",
        library_id="example", library_version=1, trader="simnow", enabled=True,
        params=dict(code="SHFE.rb.2701", capital_limit=100000, margin_fraction=.4, risk_fraction=.02,
                    custom_option="preserved"))])


class ContractPlanTests(unittest.TestCase):
    def test_multiple_contracts_divide_totals_keep_old_identity_and_do_not_mutate_input(self):
        original = plan(); before = deepcopy(original)
        result = contract_plan(original, "trend", ["SHFE.rb.2701", "SHFE.rb.2705"], CATALOG)
        self.assertEqual(original, before)
        rows = result["plan"]["strategies"]
        self.assertEqual(rows[0]["id"], "trend")
        self.assertNotEqual(rows[0]["id"], rows[1]["id"])
        self.assertEqual({row["contract_group"] for row in rows}, {"trend"})
        for row in rows:
            self.assertAlmostEqual(row["params"]["capital_limit"]*row["params"]["margin_fraction"],20000)
            self.assertAlmostEqual(row["params"]["capital_limit"]*row["params"]["risk_fraction"],1000)
            self.assertEqual(row["params"]["custom_option"],"preserved")
        self.assertEqual(result["added"],["SHFE.rb.2705"])

    def test_remove_reallocates_same_total_and_can_remove_whole_family(self):
        added = contract_plan(plan(), "trend", ["SHFE.rb.2701", "SHFE.rb.2705", "SHFE.rb.2710"], CATALOG)["plan"]
        child = added["strategies"][1]["id"]
        kept = contract_plan(added, child, ["SHFE.rb.2705"], CATALOG)
        row = kept["plan"]["strategies"][0]
        self.assertEqual(row["id"],child)
        self.assertAlmostEqual(row["params"]["margin_fraction"],.4)
        self.assertAlmostEqual(row["params"]["risk_fraction"],.02)
        self.assertEqual(len(kept["removed"]),2)
        empty = contract_plan(kept["plan"], child, [], CATALOG)
        self.assertEqual(empty["plan"]["strategies"],[])

    def test_other_product_unknown_contract_or_missing_budget_cannot_be_added(self):
        for codes in (["SHFE.cu.2701"],["SHFE.rb.2999"]):
            with self.assertRaisesRegex(ValueError,"同品种"):
                contract_plan(plan(),"trend",codes,CATALOG)
        current=plan();del current["strategies"][0]["params"]["risk_fraction"]
        with self.assertRaisesRegex(ValueError,"预算"):
            contract_plan(current,"trend",["SHFE.rb.2701","SHFE.rb.2705"],CATALOG)

    def test_names_are_unique_and_other_strategy_rows_are_preserved(self):
        current=plan();other=deepcopy(current["strategies"][0]);other.update(id="trend_SHFE_rb_2705",name="其他逻辑")
        current["strategies"].append(other)
        result=contract_plan(current,"trend",["SHFE.rb.2701","SHFE.rb.2705"],CATALOG)
        rows=result["plan"]["strategies"]
        self.assertEqual(len(set(row["id"] for row in rows)),3)
        self.assertEqual(rows[-1],other)

    def test_only_explicit_contract_parameters_are_public(self):
        self.assertEqual(contract_codes(dict(code="SHFE.rb.2701",password="private-secret")),["SHFE.rb.2701"])
        self.assertEqual(contract_codes(dict(codes=["SHFE.rb.2701","DCE.i.2701","SHFE.rb.2701"])),
                         ["SHFE.rb.2701","DCE.i.2701"])
        self.assertEqual(contract_codes(dict(code="private-secret",path="/secret/config")),[])


if __name__ == "__main__":unittest.main()
