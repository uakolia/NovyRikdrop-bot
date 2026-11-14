import unittest
from price_catalog_live import choose_tabs, GROUPS, stock_quantity, assemble

class SolutionTests(unittest.TestCase):
    def props(self):
        return [{'title': names[0], 'sheetId': i} for i,names in enumerate(GROUPS)]
    def test_allowlist(self):
        tabs=self.props()+[{'title':'Box backup 123','sheetId':100}, {'title':'Замовлення','sheetId':101}]
        self.assertEqual(len(choose_tabs(tabs)),10)
    def test_case_spacing(self):
        tabs=self.props();tabs[0]['title']='  ЯЛИНКИ   НА КРАБАХ '
        self.assertEqual(len(choose_tabs(tabs)),10)
    def test_old_titles(self):
        self.assertEqual(len(choose_tabs([{'title':n[-1],'sheetId':i} for i,n in enumerate(GROUPS)])),10)
    def test_missing_tab_blocks(self):
        with self.assertRaises(ValueError): choose_tabs(self.props()[:-1])
    def test_ambiguous_tab_blocks(self):
        with self.assertRaises(ValueError): choose_tabs(self.props()+[{'title':'Wreaths','sheetId':100}])
    def test_quantities(self):
        self.assertEqual(stock_quantity('30'),30)
        self.assertEqual(stock_quantity('0'),0)
    def test_negative_text(self):
        self.assertEqual(stock_quantity('Не наявні'),0)
    def test_positive_not_fake_quantity(self):
        self.assertIsNone(stock_quantity('Наявні'))
    def test_dates_not_quantities(self):
        self.assertIsNone(stock_quantity('Очікується 20.11'))
    def test_blank_unknown(self):
        self.assertIsNone(stock_quantity(''))
    def test_duplicate_blocks(self):
        with self.assertRaises(ValueError):
            assemble([({'title':'a','sheetId':1},[1]),({'title':'b','sheetId':2},[1])],lambda rows:[{'article':'Cr3-150'}])
    def test_empty_blocks(self):
        with self.assertRaises(ValueError): assemble([({'title':'a','sheetId':1},[])],lambda rows:[])
    def test_exact_case_distinct(self):
        m,_=assemble([({'title':'a','sheetId':1},[1])],lambda rows:[{'article':'Cr3-150'},{'article':'cr3-150'}])
        self.assertEqual(len(m),2)

if __name__=='__main__':unittest.main()
